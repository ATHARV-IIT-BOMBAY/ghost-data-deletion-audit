"""Log evidence parser: every .log line → at most one Observation. Logs never yield Coverage.

Line syntaxes (one tolerant tokenizer, `tokenize`):
  kv       ts [LEVEL] k=v k="v" ...                      (service_events, resource_lookup_trace,
                                                          support_replica, record_lookup diagnostics)
  json     ts {"severity":..,"service":..,"event":..}      (structured_service_events)
  syslog   ts <pri>1 host service trace - - k="v" ...     (syslog_archive)
  bracket  ts [LEVEL] [service] [trace] event: k="v" ...  (application_trace)
  pipe     ts | LEVEL | service | trace | event | k="v"   (operations_stream)
  cloud    ts requestId=.. level=.. function=svc message="k=\\"v\\" ..."  (cloud_runtime_export)

Interpretation: lines sharing a trace_id/requestId form one lookup. Every identifier on any line of
the trace (lookup_key, match_key, subject_key, scan_key, subject_ref, owner_key, correlation_key,
subject_reference, matched_value, record fields) goes through models.ident() unchanged and is
attached to the outcome line's Observation. No fuzzy matching: decoys like `x.jr@` stay distinct.

Event → state (events not listed are infrastructure noise and emit nothing):
  profile_lookup_completed        rows=0|http 404 → ABSENT; rows≥1 → PRESENT, IDENTIFIABLE if the
                                  line carries a non-null email/phone
  index_query_completed           result=miss → ABSENT; hit → PRESENT / IDENTIFIABLE (email field)
  audit_record_read_completed     rows=0 → ABSENT; rows≥1 → PRESENT (retention_until in note)
  analytics_profile_read          direct_identifiers=0 → ANONYMIZED, >0 → IDENTIFIABLE
  analytics_profile_read_completed  any direct PII → IDENTIFIABLE; only customer_reference → REDACTED;
                                  all null → ANONYMIZED
  analytics_identity_scan, message_pii_scan, attachment_pii_scan
                                  matched_value found by direct_identifier detector → IDENTIFIABLE
  contact_lookup_completed        rows≥1 matched by email hash → IDENTIFIABLE (email_hash); 0 → ABSENT
  message_read                    message_text=[REDACTED] → REDACTED, else IDENTIFIABLE
  support_ticket_read_completed   same PII rule as analytics_profile_read_completed
  contact_query_completed         (support replica) rows=0 → ABSENT, ≥1 → PRESENT
  deletion_marker_check           marker_found=false → PRESENT, true → ABSENT
  export_download_check_completed / export_metadata_read_completed
                                  available=false → ABSENT; true → PRESENT / IDENTIFIABLE (owner_email)
  retained_order_loaded           → PRESENT (legal hold)
  connector_heartbeat / connector_config_loaded / connector_config_read_completed
                                  enabled=true → ACTIVE, false → DISABLED
  restore_candidate_evaluated     eligible=true & deletion_block=false → RESTORABLE, else BLOCKED
  restore_authorization_completed decision=deny → BLOCKED, allow → RESTORABLE
  restore_policy_read_completed   restore_allowed=true → RESTORABLE, false → BLOCKED
  record_lookup                   result FOUND→PRESENT, NOT_FOUND→ABSENT, IDENTIFIABLE, ANONYMIZED,
                                  REDACTED as-is; ACTIVE→ACTIVE (DATA_FLOW) / RESTORABLE (BACKUP_ARCHIVE)
Ignored: *_started, cache_lookup (lookup-cache miss, not the record), connection_acquired,
dependency_*, thumbnail_failed (preview error proves nothing), batch/checkpoint, and every
log_router / dns_cache / webhook / collector / scheduler / kernel line.

system_id = record_lookup `system`, else the line's service when it is a data system, else
models.system_for(resource_id) (so profile_service → account_service, support_replica →
support_platform with the replica named in note).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from ..models import RESOURCE_PREFIX_SYSTEM, Coverage, Observation, ident, iso, system_for

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$")
_KV = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
_BRACKET = re.compile(r"^\[(\w+)\]\s+\[([^\]]*)\]\s+\[([^\]]*)\]\s+(\w+):\s*(.*)$")
_SYSLOG = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s*(.*)$")
_LEVEL = re.compile(r"^(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\s+(.*)$")

DATA_SYSTEMS = frozenset(RESOURCE_PREFIX_SYSTEM.values())
NULLS = {"", "null", "none", "[redacted]"}

# Fields that can carry a person identifier (lookup keys and record fields alike).
ID_FIELDS = ("lookup_key", "match_key", "subject_key", "scan_key", "subject_ref", "owner_key",
             "correlation_key", "subject_reference", "matched_value", "customer_reference",
             "customer_email", "email", "phone", "email_hash", "owner_email",
             "requester_email", "requester_phone")
# Record fields → PII kind they expose when non-null.
PII_FIELDS = {"email": "email", "customer_email": "email", "owner_email": "email",
              "requester_email": "email", "phone": "phone", "requester_phone": "phone",
              "email_hash": "email_hash", "requester_name": "name"}
USER_FIELDS = ("customer_reference", "subject_ref", "subject_reference")

CATEGORY = {
    "profile_lookup_completed": "USER_PROFILE",
    "index_query_completed": "SEARCH_INDEX_ENTRY",
    "audit_record_read_completed": "SECURITY_AUDIT_LOG",
    "analytics_profile_read": "ANALYTICS_PROFILE",
    "analytics_profile_read_completed": "ANALYTICS_PROFILE",
    "analytics_identity_scan": "ANALYTICS_PROFILE",
    "contact_lookup_completed": "ANALYTICS_PROFILE",
    "message_pii_scan": "SUPPORT_MESSAGE",
    "message_read": "SUPPORT_MESSAGE",
    "support_ticket_read_completed": "SUPPORT_MESSAGE",
    "attachment_pii_scan": "SUPPORT_ATTACHMENT",
    "contact_query_completed": "",
    "deletion_marker_check": "",
    "export_download_check_completed": "USER_EXPORT",
    "export_metadata_read_completed": "USER_EXPORT",
    "retained_order_loaded": "LEGAL_HOLD_RECORD",
    "connector_heartbeat": "DATA_FLOW",
    "connector_config_loaded": "DATA_FLOW",
    "connector_config_read_completed": "DATA_FLOW",
    "restore_candidate_evaluated": "BACKUP_ARCHIVE",
    "restore_authorization_completed": "BACKUP_ARCHIVE",
    "restore_policy_read_completed": "BACKUP_ARCHIVE",
    "record_lookup": "",
}


# ---------- tokenizer ----------

def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return re.sub(r"\\(.)", r"\1", v[1:-1])
    return v


def _kv(text: str) -> dict[str, str]:
    out = {k: _unquote(v) for k, v in _KV.findall(text)}
    if "=" in out.get("message", ""):  # cloud runtime: nested k=\"v\" inside message="..."
        inner = _kv(out.pop("message"))
        out.update({k: v for k, v in inner.items() if k not in out})
    return out


def tokenize(line: str) -> tuple[str, str, dict[str, str]] | None:
    """Return (timestamp, format, fields) for one log line, or None if it is not a log record."""
    m = _TS.match(line.strip())
    if not m:
        return None
    ts, rest = m.groups()
    if rest.startswith("{"):
        try:
            d = json.loads(rest)
        except json.JSONDecodeError:
            return None
        return ts, "json", {k: "" if v is None else str(v) for k, v in d.items()}
    if rest.startswith("["):
        b = _BRACKET.match(rest)
        if not b:
            return None
        level, svc, trace, event, kv = b.groups()
        return ts, "bracket", {"level": level, "service": svc, "trace_id": trace, "event": event, **_kv(kv)}
    if rest.startswith("|"):
        rest = rest[1:].lstrip()
    parts = [p.strip() for p in rest.split(" | ")]
    if len(parts) >= 5 and _LEVEL.match(parts[0] + " x"):
        level, svc, trace, event, *kv = parts
        return ts, "pipe", {"level": level, "service": svc, "trace_id": trace, "event": event, **_kv(" | ".join(kv))}
    if rest.startswith("<"):
        s = _SYSLOG.match(rest)
        if not s:
            return None
        host, app, proc, kv = s.groups()
        return ts, "syslog", {"host": host, "service": app, "trace_id": proc, **_kv(kv)}
    lv = _LEVEL.match(rest)
    if lv:
        return ts, "kv", {"level": lv.group(1), **_kv(lv.group(2))}
    f = _kv(rest)
    if not f:
        return None
    return ts, ("cloud" if "requestId" in f else "kv"), f


# ---------- interpretation ----------

def _val(f: dict[str, str], k: str) -> str:
    v = f.get(k, "")
    return "" if v.strip().lower() in NULLS else v.strip()


def _int(f: dict[str, str], k: str) -> int | None:
    try:
        return int(f[k])
    except (KeyError, ValueError):
        return None


def _pii(f: dict[str, str]) -> set[str]:
    kinds = {kind for k, kind in PII_FIELDS.items() if _val(f, k)}
    mv = ident(_val(f, "matched_value"))
    if mv:
        kinds.add({"email": "email", "phone": "phone", "hash": "email_hash", "user": "user_id"}[mv.split(":")[0]])
    if any(_val(f, k) for k in USER_FIELDS):
        kinds.add("user_id")
    return kinds


def _pii_state(f: dict[str, str]) -> str:
    """Record read with explicit PII fields: direct PII → IDENTIFIABLE, only user ref → REDACTED."""
    kinds = _pii(f)
    if kinds - {"user_id"}:
        return "IDENTIFIABLE"
    return "REDACTED" if "user_id" in kinds else "ANONYMIZED"


def _present(f: dict[str, str]) -> str:
    return "IDENTIFIABLE" if _pii(f) - {"user_id"} else "PRESENT"


def _found(f: dict[str, str]) -> bool | None:
    """rows / http_status / result / available → found?  None when the line states no outcome."""
    rows, http = _int(f, "rows"), _int(f, "http_status")
    if rows is not None:
        return rows > 0
    if http is not None:
        return http < 400
    res = f.get("result", "").lower()
    if res in ("hit", "found"):
        return True
    if res in ("miss", "not_found"):
        return False
    if "available" in f:
        return f["available"].lower() == "true"
    return None


def _true(f: dict[str, str], k: str) -> bool:
    return f.get(k, "").lower() == "true"


def state_for(event: str, f: dict[str, str]) -> str | None:
    if event == "record_lookup":
        res = f.get("result", "").upper()
        if res == "ACTIVE":
            return "RESTORABLE" if f.get("record_type") == "BACKUP_ARCHIVE" else "ACTIVE"
        return {"FOUND": "PRESENT", "NOT_FOUND": "ABSENT"}.get(res, res if res in
                ("IDENTIFIABLE", "ANONYMIZED", "REDACTED", "DISABLED", "BLOCKED", "RESTORABLE") else None)
    if event.startswith("connector_") and "enabled" in f:
        return "ACTIVE" if _true(f, "enabled") else "DISABLED"
    if event == "restore_candidate_evaluated":
        return "RESTORABLE" if _true(f, "eligible") and not _true(f, "deletion_block") else "BLOCKED"
    if event == "restore_authorization_completed":
        return "BLOCKED" if f.get("decision", "").lower() == "deny" else "RESTORABLE"
    if event == "restore_policy_read_completed":
        return "RESTORABLE" if _true(f, "restore_allowed") else "BLOCKED"
    if event in ("analytics_profile_read_completed", "support_ticket_read_completed"):
        return _pii_state(f)
    if event == "analytics_profile_read":
        return "IDENTIFIABLE" if (_int(f, "direct_identifiers") or 0) > 0 or _pii(f) - {"user_id"} else "ANONYMIZED"
    if event in ("analytics_identity_scan", "message_pii_scan", "attachment_pii_scan"):
        return "IDENTIFIABLE"
    if event == "message_read":
        return "REDACTED" if f.get("message_text", "").strip().lower() in NULLS else "IDENTIFIABLE"
    if event == "contact_lookup_completed":
        return "IDENTIFIABLE" if _found(f) else "ABSENT"
    if event == "deletion_marker_check":
        return "ABSENT" if _true(f, "marker_found") else "PRESENT"
    if event == "retained_order_loaded":
        return "PRESENT"
    if event in ("profile_lookup_completed", "index_query_completed", "audit_record_read_completed",
                 "contact_query_completed", "export_download_check_completed",
                 "export_metadata_read_completed"):
        found = _found(f)
        if found is None:
            return None
        return _present(f) if found else "ABSENT"
    return None


def _line_idents(f: dict[str, str]) -> set[str]:
    return {t for k in ID_FIELDS if (t := ident(_val(f, k)))}


def _system(f: dict[str, str], rid: str) -> str:
    if f.get("system"):
        return f["system"]
    svc = f.get("service") or f.get("function") or f.get("logger") or ""
    return svc if svc in DATA_SYSTEMS else system_for(rid)


def _note(f: dict[str, str], fmt_trace: str) -> str:
    bits = [f"trace={fmt_trace}"] if fmt_trace else []
    svc = f.get("service") or f.get("function") or ""
    if svc and svc not in DATA_SYSTEMS:
        bits.append(f"via={svc}")
    for k in ("pool", "region", "index", "source", "retention_until", "event_code", "connector_name",
              "last_delivery_status", "last_run", "file", "object_key", "archive_key", "policy",
              "reason", "last_write", "record_type", "audience_cohort", "detector"):
        if f.get(k):
            bits.append(f"{k}={f[k]}")
    return " ".join(bits)


def parse(path: Path, file_id: str, captured_at: str) -> tuple[list[Observation], list[Coverage]]:
    rows = [t for line in path.read_text(encoding="utf-8").splitlines() if (t := tokenize(line))]
    trace_ids: dict[str, set[str]] = defaultdict(set)
    trace_pool: dict[str, str] = {}
    for _, _, f in rows:
        tr = f.get("trace_id") or f.get("requestId") or ""
        if tr:
            trace_ids[tr] |= _line_idents(f)
            if f.get("pool"):
                trace_pool[tr] = f["pool"]
    obs: list[Observation] = []
    for ts, fmt, f in rows:
        event, rid = f.get("event", ""), _val(f, "resource_id")
        if not rid or event not in CATEGORY:
            continue
        state = state_for(event, f)
        if state is None:
            continue
        tr = f.get("trace_id") or f.get("requestId") or ""
        ids = _line_idents(f) | trace_ids.get(tr, set())
        if tr in trace_pool and "pool" not in f:
            f = {**f, "pool": trace_pool[tr]}
        obs.append(Observation(
            file_id=file_id,
            observed_at=iso(ts),
            system_id=_system(f, rid),
            resource_id=rid,
            state=state,
            identifiers=frozenset(ids),
            pii=frozenset(_pii(f)),
            category=f.get("record_type") or CATEGORY[event],
            source=f"log:{fmt}:{event}",
            note=_note(f, tr),
        ))
    return obs, []


def _demo() -> None:
    import csv
    from collections import Counter
    root = Path(__file__).resolve().parents[3] / "dataset"
    hist: Counter[str] = Counter()
    by_rid: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    noise: list[str] = []
    with open(root / "evidence_files.csv", newline="", encoding="utf-8") as fh:
        manifest = [r for r in csv.DictReader(fh) if r["file_path"].endswith(".log")]
    for r in manifest:
        p = root / r["file_path"]
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        unparsed = [l for l in lines if tokenize(l) is None]
        o, c = parse(p, r["file_id"], r["captured_at"])
        assert not c
        for x in o:
            hist[x.state] += 1
            by_rid[(x.file_id, x.resource_id)].append(x)
        for l in lines:
            t = tokenize(l)
            if t and not (t[2].get("event") in CATEGORY and _val(t[2], "resource_id")):
                noise.append(l)
        print(f"{r['file_id']} {p.name:52} lines={len(lines):4} obs={len(o):4} unparsed={len(unparsed)}")
    print("states:", dict(hist.most_common()))
    print("sample non-observation lines:")
    for l in noise[::max(1, len(noise) // 5)][:5]:
        print("  ", l[:160])

    def one(fid: str, rid: str) -> Observation:
        return by_rid[(fid, rid)][-1]
    assert one("evidence_01", "data_flow_9846").state == "ACTIVE"
    a = one("evidence_94", "account_7566")
    assert a.state == "ABSENT" and "email:l.fontaine46@yahoo.com" in a.identifiers, a
    b = one("evidence_94", "audit_record_2016")
    assert b.state == "PRESENT" and "user:user_43" in b.identifiers and b.system_id == "security_service", b
    assert one("evidence_94", "analytics_9406").state == "ANONYMIZED"
    s = [x for x in by_rid[("evidence_01", "search_1874")]][0]
    assert "email:g.silva76.jr@gmail.com" in s.identifiers and "email:g.silva76@gmail.com" not in s.identifiers
    with open(root / "sample_output.csv", newline="", encoding="utf-8") as fh:
        gold = {(st["evidence_id"], st["resource_id"]) for row in csv.DictReader(fh)
                for st in json.loads(row["steps_json"])}
    log_ids = {r["file_id"] for r in manifest}
    want = {g for g in gold if g[0] in log_ids}
    got = {g for g in want if g in by_rid}
    print(f"gold log citations: {len(got)}/{len(want)} emitted; missing={sorted(want - got)}")
    print("gold rids emitted:", sorted(f"{e}:{r}={by_rid[(e, r)][-1].state}" for e, r in got))


if __name__ == "__main__":
    _demo()
