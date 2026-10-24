"""Parser for the JSON evidence files under dataset/evidence/records/.

Families (detected by top-level keys):
  bundle   {snapshots:[{captured_at, data_store, collection, records}]}
           Each snapshot is a full dump of one collection -> one Coverage row (empty list = absence
           proof, e.g. evidence_51 account_service/customer_profiles). Exception: the
           `operational_record_history` collections are lookup logs (one row per lookup, own `system`
           per row), so they are parsed like ledgers and get no Coverage.
  service  {service_exports:{<system>:{<list>:[{observed_at, resource_id, ...}]}}}
           Per-subject lookups (profile_lookups 404 -> ABSENT, index_queries, ...). They sample a
           few resources, so a missing resource proves nothing -> no Coverage.
  ledger   {origin, records:[{recorded_at, system, resource_id, record_type, result, subject_reference}]}
           Manifests / ledgers / connector configuration logs: per-resource results, not listings
           -> no Coverage.
  store    {captured_at, source, collection, records:[...]}
           A capture of one store collection -> Coverage.

Ledger `result` -> OBS_STATES (RESULT_STATE below): FOUND->PRESENT, NOT_FOUND->ABSENT,
IDENTIFIABLE/ANONYMIZED/REDACTED/BLOCKED/DISABLED unchanged, ACTIVE->RESTORABLE for
BACKUP_ARCHIVE and ACTIVE for anything else (DATA_FLOW).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from ghostaudit.models import Coverage, Observation, idents, iso, system_for

RESULT_STATE = {
    "FOUND": "PRESENT",
    "NOT_FOUND": "ABSENT",
    "IDENTIFIABLE": "IDENTIFIABLE",
    "ANONYMIZED": "ANONYMIZED",
    "REDACTED": "REDACTED",
    "BLOCKED": "BLOCKED",
    "DISABLED": "DISABLED",
    "ACTIVE": "ACTIVE",
}

_TEXT_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TEXT_PHONE = re.compile(r"\+\d[\d\s().-]{6,}\d")

# field name -> PII kind; anything listed here is also fed to idents()
PII_FIELDS = {
    "full_name": "name", "name": "name", "display_name": "name", "requester_name": "name",
    "email": "email", "owner_email": "email", "customer_email": "email", "requester_email": "email",
    "uploaded_by": "email",
    "phone": "phone", "owner_phone": "phone", "requester_phone": "phone",
    "email_hash": "email_hash",
    "user_id": "user_id", "customer_reference": "user_id",
}
REF_FIELDS = ("subject_reference", "subject_selector", "lookup_value", "query")
TEXT_FIELDS = ("message_text", "message_excerpt")
DIRECT = frozenset({"name", "email", "phone", "email_hash"})


def _scan(rec: dict) -> tuple[frozenset[str], frozenset[str]]:
    """(identifiers, pii kinds) for a flat record; free-text emails/phones count as PII."""
    raw: list[object] = [rec.get(f) for f in REF_FIELDS]
    pii: set[str] = set()
    names: set[str] = set()
    for f, kind in PII_FIELDS.items():
        v = rec.get(f)
        if v is None or v == "":
            continue
        pii.add(kind)
        if kind == "name":
            names.add("name:" + str(v).strip().lower())
        else:
            raw.append(v)
    for f in TEXT_FIELDS:
        text = rec.get(f) or ""
        found = _TEXT_EMAIL.findall(text) + _TEXT_PHONE.findall(text)
        raw += found
        pii |= {"email" if "@" in t else "phone" for t in found}
    return idents(*raw) | names, frozenset(pii)


def _exists(pii: frozenset[str], gone: str = "PRESENT") -> str:
    """State of a record that exists: IDENTIFIABLE if it holds a direct identifier, else `gone`."""
    return "IDENTIFIABLE" if pii & DIRECT else gone


def _record_state(collection: str, rec: dict, pii: frozenset[str]) -> tuple[str, str]:
    """(state, note) for a row of a bundle/store collection."""
    if "restore_allowed" in rec:
        return ("RESTORABLE" if rec["restore_allowed"] else "BLOCKED"), ""
    if "enabled" in rec:
        return ("ACTIVE" if rec["enabled"] else "DISABLED"), rec.get("mode", "")
    if collection == "customer_analytics_reports":
        return ("IDENTIFIABLE" if pii else "ANONYMIZED"), ""
    if collection in ("deleted_customer_references", "support_conversations"):
        return _exists(pii, "REDACTED"), ""
    if collection == "customer_export_jobs":
        return "PRESENT", f"download_available={rec.get('download_available')} expires_at={rec.get('expires_at')}"
    if collection == "retained_order_records":
        return "PRESENT", f"retained_until={rec.get('retained_until')} reason={rec.get('retention_reason')}"
    if collection in ("security_audit_events", "ticket_attachments"):
        return "PRESENT", ""
    return _exists(pii), ""


CATEGORY = {
    "customer_profiles": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_export_jobs": "USER_EXPORT",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD",
    "backup_catalog": "BACKUP_ARCHIVE",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "connector_configurations": "DATA_FLOW",
    "profile_lookups": "USER_PROFILE",
    "index_queries": "SEARCH_INDEX_ENTRY",
    "profiles": "ANALYTICS_PROFILE",
    "tickets": "SUPPORT_MESSAGE",
    "exports": "USER_EXPORT",
    "audit_records": "SECURITY_AUDIT_LOG",
    "restore_entries": "BACKUP_ARCHIVE",
    "connectors": "DATA_FLOW",
}


def _flatten(rec: dict) -> dict:
    """Lift a nested search `document` into the row so _scan sees its fields."""
    doc = rec.get("document")
    return {**rec, **doc} if isinstance(doc, dict) else rec


def _collection(file_id: str, at: str, system: str, collection: str, records: list[dict],
                family: str) -> tuple[list[Observation], Coverage]:
    obs = []
    for raw in records:
        rec = _flatten(raw)
        ids, pii = _scan(rec)
        state, note = _record_state(collection, rec, pii)
        obs.append(Observation(file_id, at, system or system_for(rec["resource_id"]), rec["resource_id"],
                               state, ids, pii, CATEGORY.get(collection, ""),
                               f"json:{family}:{collection}", note))
    cov_system = system or (system_for(records[0]["resource_id"]) if records else "")
    return obs, Coverage(file_id, at, cov_system, collection, frozenset(o.resource_id for o in obs))


def _ledger_rows(file_id: str, records: list[dict], family: str, name: str) -> list[Observation]:
    obs = []
    for rec in records:
        result, rtype = rec["result"], rec["record_type"]
        if result not in RESULT_STATE:
            raise ValueError(f"{file_id}: unmapped ledger result {result!r}")
        state = "RESTORABLE" if (result == "ACTIVE" and rtype == "BACKUP_ARCHIVE") else RESULT_STATE[result]
        ids, _ = _scan(rec)
        obs.append(Observation(file_id, iso(rec["recorded_at"]), rec.get("system") or system_for(rec["resource_id"]),
                               rec["resource_id"], state, ids, frozenset(), rtype,
                               f"json:{family}:{name}", f"result={result}"))
    return obs


def _service_row(system: str, lst: str, rec: dict) -> tuple[str, frozenset[str], frozenset[str], str]:
    """(state, identifiers, pii, note) for one service_exports lookup row."""
    if lst == "profile_lookups":
        prof = rec.get("profile")
        if rec.get("status_code") == 404 or prof is None:
            return "ABSENT", frozenset(), frozenset(), f"status_code={rec.get('status_code')}"
        ids, pii = _scan(prof)
        return _exists(pii), ids, pii, f"status_code={rec.get('status_code')}"
    if lst == "index_queries":
        hits = rec.get("hits") or {}
        docs = [d for d in hits.get("documents", []) if d.get("resource_id") in (None, rec["resource_id"])]
        if not hits.get("total") or not docs:
            ids, _ = _scan(rec)
            return "ABSENT", ids, frozenset(), "total=0"
        ids, pii = _scan({**docs[0], "query": rec.get("query")})
        return _exists(pii), ids, pii, f"query={rec.get('query')}"
    ids, pii = _scan(rec)
    if "restore_allowed" in rec:
        return ("RESTORABLE" if rec["restore_allowed"] else "BLOCKED"), ids, pii, ""
    if "enabled" in rec:
        return ("ACTIVE" if rec["enabled"] else "DISABLED"), ids, pii, f"last_delivery_status={rec.get('last_delivery_status')}"
    if lst == "profiles":  # analytics: a stable user ref alone still links the profile to a person
        return ("IDENTIFIABLE" if pii else "ANONYMIZED"), ids, pii, ""
    if lst == "tickets":
        return _exists(pii, "REDACTED"), ids, pii, ""
    if lst == "audit_records":
        return "PRESENT", ids, pii, f"retention_until={rec.get('retention_until')} event={rec.get('event_code')}"
    return _exists(pii), ids, pii, ""


def _service(file_id: str, captured_at: str, exports: dict) -> list[Observation]:
    obs = []
    for system, lists in exports.items():
        for lst, rows in lists.items():
            for rec in rows:
                state, ids, pii, note = _service_row(system, lst, rec)
                obs.append(Observation(file_id, iso(rec.get("observed_at") or captured_at), system,
                                       rec["resource_id"], state, ids, pii, CATEGORY.get(lst, ""),
                                       f"json:service:{lst}", note))
    return obs


def parse(path: Path, file_id: str, captured_at: str) -> tuple[list[Observation], list[Coverage]]:
    data = json.loads(Path(path).read_text())
    obs: list[Observation] = []
    cov: list[Coverage] = []
    if "snapshots" in data:
        for snap in data["snapshots"]:
            at = iso(snap.get("captured_at") or captured_at)
            if snap["collection"] == "operational_record_history":
                obs += _ledger_rows(file_id, snap["records"], "bundle", snap["collection"])
                continue
            o, c = _collection(file_id, at, snap["data_store"], snap["collection"], snap["records"], "bundle")
            obs += o
            cov.append(c)
    elif "service_exports" in data:
        obs += _service(file_id, captured_at, data["service_exports"])
    elif "collection" in data:
        o, c = _collection(file_id, iso(data.get("captured_at") or captured_at), "", data["collection"],
                           data["records"], "store")
        obs += o
        cov.append(c)
    elif "records" in data:
        obs += _ledger_rows(file_id, data["records"], "ledger", data.get("origin", "records"))
    else:
        raise ValueError(f"{file_id}: unknown JSON evidence shape {sorted(data)}")
    return obs, cov


def _main() -> None:
    import csv
    root = Path(__file__).resolve().parents[3] / "dataset"
    all_obs: list[Observation] = []
    all_cov: list[Coverage] = []
    for row in csv.DictReader(open(root / "evidence_files.csv")):
        if not row["file_path"].endswith(".json"):
            continue
        o, c = parse(root / row["file_path"], row["file_id"], row["captured_at"])
        print(f"{row['file_id']} {Path(row['file_path']).name}: obs={len(o)} cov={len(c)}")
        all_obs += o
        all_cov += c
    print("states:", dict(Counter(o.state for o in all_obs)))
    print("observations:", len(all_obs), "coverage:", len(all_cov))
    got = {(o.file_id, o.resource_id): o.state for o in all_obs}
    checks = [("evidence_51", "tombstone_440", "REDACTED"), ("evidence_51", "audit_record_6909", "PRESENT"),
              ("evidence_51", "tombstone_9178", "IDENTIFIABLE"), ("evidence_92", "account_5684", "ABSENT"),
              ("evidence_92", "audit_record_5416", "PRESENT"), ("evidence_92", "support_4715", "REDACTED"),
              ("evidence_92", "analytics_4566", "ANONYMIZED"), ("evidence_92", "data_flow_6244", "DISABLED"),
              ("evidence_92", "backup_3120", "BLOCKED")]
    for f, r, s in checks:
        assert got.get((f, r)) == s, (f, r, got.get((f, r)), s)
    assert any(c.file_id == "evidence_51" and c.system_id == "account_service"
               and c.scope == "customer_profiles" and not c.resource_ids for c in all_cov)
    print("gold spot checks: ok", file=sys.stderr)


if __name__ == "__main__":
    _main()
