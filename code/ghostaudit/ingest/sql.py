"""Parser for SQLite point-in-time dumps under dataset/evidence/databases/.

Schema families (each dump is loaded into an in-memory sqlite3 db; nothing else is executed):
  inventory  customer_data_inventory_*.sql: one table per collection (customer_profiles,
             customer_search_documents, customer_analytics_profiles, support_records, customer_exports,
             connector_subscriptions, backup_restore_entries, security_audit_records) plus snapshot_scope
             (origin_system, captured_at) naming which systems the dump fully covers.
  topology   customer_data_{read,regional,reporting,failover}_replica*.sql: customer_data_primary plus
             replica tables, all (resource_id, origin_system, collection_name, record_json, updated_at).
             A record present in a replica but missing from primary is replication drift.
  profiles   customer_profiles_primary / _failover_replica and search_documents_primary: flat columns.

observed_at is the capture time (snapshot_scope for that system, else the "-- Captured at:" header,
else the captured_at passed in), never updated_at: updated_at is a last-write time, not the moment the
row was seen; in topology dumps it often equals capture time anyway. updated_at goes into `note`.

Coverage: every table is a complete listing at capture time. Topology tables mix collections, so we emit
one Coverage per (table, collection) for every collection that appears anywhere in the file; a replica
holding customer_profiles while primary holds none yields an empty primary coverage (= drift proof).
Inventory tables only get Coverage when their system is listed in snapshot_scope.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ghostaudit.models import Coverage, Observation, ident, iso, system_for

# table or collection name -> (category, kind)
KINDS = {
    "customer_profiles": ("USER_PROFILE", "account"),
    "customer_search_documents": ("SEARCH_INDEX_ENTRY", "search"),
    "search_documents": ("SEARCH_INDEX_ENTRY", "search"),
    "customer_analytics_profiles": ("ANALYTICS_PROFILE", "analytics"),
    "customer_analytics_reports": ("ANALYTICS_PROFILE", "analytics"),
    "support_records": ("SUPPORT_MESSAGE", "support"),
    "support_conversations": ("SUPPORT_MESSAGE", "support"),
    "ticket_attachments": ("SUPPORT_ATTACHMENT", "attachment"),
    "customer_exports": ("USER_EXPORT", "export"),
    "customer_export_jobs": ("USER_EXPORT", "export"),
    "connector_subscriptions": ("", "flow"),
    "connector_configurations": ("", "flow"),
    "backup_restore_entries": ("BACKUP_ARCHIVE", "backup"),
    "backup_catalog": ("BACKUP_ARCHIVE", "backup"),
    "security_audit_records": ("SECURITY_AUDIT_LOG", "audit"),
    "security_audit_events": ("SECURITY_AUDIT_LOG", "audit"),
    "retained_order_records": ("LEGAL_HOLD_RECORD", "legal"),
    "deleted_customer_references": ("ACCOUNT_TOMBSTONE", "tombstone"),
}
INVENTORY_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
}
NAME_FIELDS = ("full_name", "display_name", "requester_name", "name")
TEXT_FIELDS = ("message_text", "message_excerpt")
SKIP_FIELDS = {"resource_id", "updated_at", *NAME_FIELDS, *TEXT_FIELDS}
DIRECT_PII = {"name", "email", "phone", "email_hash"}
TOKEN_KIND = {"email": "email", "phone": "phone", "hash": "email_hash", "user": "user_id", "name": "name"}
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_IN_TEXT = re.compile(r"\+\d[\d ()-]{6,}\d")
_HEADER = re.compile(r"^--\s*(Captured at|Database topology):\s*(\S+)", re.M)


def _truthy(v: object) -> bool:
    return str(v).strip().lower() in ("1", "true")


def _identifiers(rec: dict) -> frozenset[str]:
    """Typed tokens from every scalar field, emails/phones inside free text, and name:<lower> tokens."""
    raw: list[object] = [v for k, v in rec.items() if k not in SKIP_FIELDS and not isinstance(v, (dict, list))]
    for f in TEXT_FIELDS:
        raw += _EMAIL_IN_TEXT.findall(rec.get(f) or "") + _PHONE_IN_TEXT.findall(rec.get(f) or "")
    toks = {t for t in map(ident, raw) if t}
    toks |= {"name:" + " ".join(str(rec[f]).lower().split()) for f in NAME_FIELDS if rec.get(f)}
    return frozenset(toks)


def _state(kind: str, rec: dict, pii: frozenset[str]) -> str:
    if kind == "flow":
        return "ACTIVE" if _truthy(rec.get("enabled")) else "DISABLED"
    if kind == "backup":
        return "RESTORABLE" if _truthy(rec.get("restore_allowed")) else "BLOCKED"
    if kind in ("analytics", "support", "tombstone"):
        if pii & DIRECT_PII:
            return "IDENTIFIABLE"
        return "ANONYMIZED" if kind == "analytics" else "REDACTED"
    # account/search/export/attachment/audit/legal: the row exists. account_status (e.g. DELETION_PENDING)
    # is a claim about the workflow, not the row, so it only goes into note.
    return "PRESENT"


def _note(rec: dict, extra: dict[str, str]) -> str:
    keep = ("account_status", "retention_until", "retained_until", "source_resource_id", "updated_at")
    parts = {**extra, **{k: str(rec[k]) for k in keep if rec.get(k) is not None}}
    return "; ".join(f"{k}={v}" for k, v in parts.items())


def _obs(file_id: str, at: str, system: str, coll: str, table: str, rec: dict, extra: dict[str, str]) -> Observation:
    category, kind = KINDS[coll]
    toks = _identifiers(rec)
    pii = frozenset(TOKEN_KIND[t.split(":", 1)[0]] for t in toks)
    rid = rec["resource_id"]
    return Observation(file_id=file_id, observed_at=at, system_id=system or system_for(rid), resource_id=rid,
                       state=_state(kind, rec, pii), identifiers=toks, pii=pii, category=category,
                       source=f"sql:{table}", note=_note(rec, extra))


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _parse_topology(conn, tables, file_id, at, topo) -> tuple[list[Observation], list[Coverage]]:
    """customer_data_<replica> tables holding JSON records from many collections."""
    obs, seen = [], {}  # seen: table -> {collection: set(ids)}
    colls: dict[str, str] = {}  # collection -> origin_system
    for t in tables:
        seen[t] = {}
        replica = t.removeprefix("customer_data_")
        for row in _rows(conn, t):
            rec = json.loads(row["record_json"])
            rec.update(rec.pop("document", None) or {})  # search docs nest their fields
            rec["resource_id"], rec["updated_at"] = row["resource_id"], row["updated_at"]
            coll, sysid = row["collection_name"], row["origin_system"]
            colls[coll] = sysid
            seen[t].setdefault(coll, set()).add(row["resource_id"])
            obs.append(_obs(file_id, at, sysid, coll, t, rec, {"replica": replica, "topology": topo}))
    cov = [Coverage(file_id, at, sysid, f"{t}.{coll}", frozenset(seen[t].get(coll, ())))
           for t in tables for coll, sysid in sorted(colls.items())]
    return obs, cov


def _parse_inventory(conn, tables, file_id, captured_at) -> tuple[list[Observation], list[Coverage]]:
    """Per-collection inventory tables; snapshot_scope says which systems are fully listed and when."""
    scope = {r["origin_system"]: iso(r["captured_at"]) for r in _rows(conn, "snapshot_scope")}
    obs, cov = [], []
    for t in tables:
        if t == "snapshot_scope":
            continue
        sysid = INVENTORY_SYSTEM[t]
        at = scope.get(sysid, captured_at)
        rows = _rows(conn, t)
        obs += [_obs(file_id, at, sysid, t, t, r, {"in_snapshot_scope": str(sysid in scope).lower()}) for r in rows]
        if sysid in scope:
            cov.append(Coverage(file_id, at, sysid, t, frozenset(r["resource_id"] for r in rows)))
    return obs, cov


def _parse_flat(conn, tables, file_id, at, topo) -> tuple[list[Observation], list[Coverage]]:
    """customer_profiles_<replica> and search_documents_<replica>: flat columns, one collection per table."""
    obs, cov = [], []
    for t in tables:
        coll = "customer_profiles" if t.startswith("customer_profiles") else "search_documents"
        replica = t.removeprefix(coll + "_")
        sysid = "account_service" if coll == "customer_profiles" else "search_service"
        rows = _rows(conn, t)
        obs += [_obs(file_id, at, sysid, coll, t, r, {"replica": replica, "topology": topo}) for r in rows]
        cov.append(Coverage(file_id, at, sysid, t, frozenset(r["resource_id"] for r in rows)))
    return obs, cov


def parse(path: Path, file_id: str, captured_at: str) -> tuple[list[Observation], list[Coverage]]:
    text = path.read_text()
    header = dict(_HEADER.findall(text))
    at = iso(header.get("Captured at", captured_at))
    topo = header.get("Database topology", "")
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(text)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if "snapshot_scope" in tables:
            return _parse_inventory(conn, tables, file_id, iso(captured_at))
        if all(t.startswith("customer_data_") for t in tables):
            return _parse_topology(conn, tables, file_id, at, topo)
        return _parse_flat(conn, tables, file_id, at, topo)
    finally:
        conn.close()


if __name__ == "__main__":
    import csv
    from collections import Counter

    dataset = Path(__file__).resolve().parents[3] / "dataset"
    states: Counter[str] = Counter()
    by_file: dict[str, tuple[list[Observation], list[Coverage]]] = {}
    for row in csv.DictReader(open(dataset / "evidence_files.csv")):
        if row["file_path"].endswith(".sql"):
            o, c = parse(dataset / row["file_path"], row["file_id"], row["captured_at"])
            by_file[row["file_id"]] = (o, c)
            states.update(x.state for x in o)
            print(f"{row['file_id']:12} {Path(row['file_path']).name:52} obs={len(o):3} cov={len(c):3}")
    print(f"files={len(by_file)} obs={sum(len(o) for o, _ in by_file.values())} states={dict(sorted(states.items()))}")

    acct = [c for c in by_file["evidence_54"][1] if c.system_id == "account_service"]
    assert acct and all(not {"account_001", "account_002", "account_004"} & c.resource_ids for c in acct)
    assert any(o.resource_id == "account_005" and "failover" in o.note for o in by_file["evidence_54"][0])
    b = [o for o in by_file["evidence_93"][0] if o.resource_id == "backup_4978"]
    assert [o.state for o in b] == ["RESTORABLE"], b
    print("spot checks OK")
