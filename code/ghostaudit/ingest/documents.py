"""TXT shift handovers and PDF business documents → Observation rows.

TXT (customer_account_handover_*.txt): one check per line
    - <ts> | <system_id> | <resource_id> | <CATEGORY> | <STATE> | <identifier or 'no direct identifier'>
A handover is a sample of checks, not a full listing, so it never yields Coverage.

PDFs are business documents, not verdicts. Each is recognised by its title and read with
explicit regexes; a document we don't recognise yields nothing (never guessed).
- Customer statement: links name/user_id/email to statement + order refs. It proves no state of
  any platform resource, so it is emitted with system_id "" (identity link only) and the
  statement number as resource_id; the engine must not plan steps for system-less rows.
- Delivery correction attachment: the support attachment `support_NNN` it names exists (PRESENT)
  on support_platform and carries the requester's name/email/phone.
- Disputed orders / tax register: one LEGAL_HOLD_RECORD per row. The register has no legal_hold_
  resource id, so the order id is the resource_id on legal_service; retain-until goes in note.
- Compliance journal extract: the audit record it names is PRESENT on security_service for the
  subject user; recorded/retain-until go in note.
observed_at is the document's own issued/generated/captured stamp when it states one (that is
when the document attests the state), else the manifest captured_at.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..models import Coverage, Observation, ident, idents, iso, system_for

# Handover STATE → OBS_STATES. ACTIVE is context-dependent (backup vs data flow).
TXT_STATE = {
    "NOT_FOUND": "ABSENT",
    "FOUND": "PRESENT",
    "IDENTIFIABLE": "IDENTIFIABLE",
    "REDACTED": "REDACTED",
    "ANONYMIZED": "ANONYMIZED",
    "DISABLED": "DISABLED",
    "BLOCKED": "BLOCKED",
    "RESTORABLE": "RESTORABLE",
}
_LINE = re.compile(r"^\s*-\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*$")
_SNAPSHOT = re.compile(r"^Snapshot:\s*(\S+)", re.M)

_MON = r"\d{1,2} [A-Z][a-z]{2} \d{4}"
_STAMP = rf"({_MON})(?:\s+(\d{{2}})\s+(\d{{2}}) UTC)?"  # pypdf drops the ':' and splits HH / MM


def _pii_of(ids: frozenset[str]) -> frozenset[str]:
    kinds = {"email": "email", "phone": "phone", "hash": "email_hash", "user": "user_id", "name": "name"}
    return frozenset(kinds[t.split(":", 1)[0]] for t in ids)


def _date(day: str, hh: str | None = None, mm: str | None = None) -> str:
    d = datetime.strptime(day, "%d %b %Y")
    return f"{d:%Y-%m-%d}T{hh or '00'}:{mm or '00'}:00Z"


def _stamp(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s+{_STAMP}", text)
    return _date(*m.groups()) if m else None


def _txt_state(state: str, category: str) -> str:
    if state == "ACTIVE":
        return "RESTORABLE" if category == "BACKUP_ARCHIVE" else "ACTIVE"
    if state not in TXT_STATE:
        raise ValueError(f"unknown handover state {state!r}")
    return TXT_STATE[state]


def parse_txt(path: Path, file_id: str, captured_at: str) -> list[Observation]:
    text = path.read_text(encoding="utf-8")
    snap = _SNAPSHOT.search(text)
    default_at = iso(snap.group(1)) if snap else iso(captured_at)
    out: list[Observation] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("-"):
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"unrecognised handover line: {line!r}")
        ts, system, rid, category, state, who = m.groups()
        ids = idents(who)
        out.append(Observation(
            file_id=file_id,
            observed_at=iso(ts) if ts else default_at,
            system_id=system or system_for(rid),
            resource_id=rid,
            state=_txt_state(state, category),
            identifiers=ids,
            pii=_pii_of(ids),
            category=category,
            source="documents.txt:handover",
            note=f"handover_state={state}",
        ))
    return out


def _name(v: str) -> str:
    return "name:" + " ".join(v.split()).lower()


def _statement(text: str, file_id: str, at: str) -> list[Observation]:
    stmt = re.search(r"Statement (NST-[\d-]+)", text)
    name = re.search(r"Customer ([A-Z][^\n]+)", text)
    user = re.search(r"\b(user_\d+)\b", text)
    email = re.search(r"\b(\S+@\S+\.\w+)\b", text)
    orders = re.findall(r"\b(ORD-\d+)\b", text)
    if not (stmt and (user or email)):
        return []
    ids = idents(user and user.group(1), email and email.group(1)) | ({_name(name.group(1))} if name else set())
    return [Observation(file_id, at, "", stmt.group(1), "PRESENT", frozenset(ids), _pii_of(frozenset(ids)),
                        "CUSTOMER_STATEMENT", "documents.pdf:customer_statement",
                        "identity link only; orders=" + ",".join(sorted(set(orders))))]


def _attachment(text: str, file_id: str, at: str) -> list[Observation]:
    ref = re.search(r"reference (CS-\d+) / (support_\d+)", text)
    if not ref:
        return []
    # The requester block is the bare Detail lines right after "received <date>".
    block = re.search(rf"received {_MON}\nDetail\n([^\n]+)\nDetail\n([^\n]+)\nDetail\n([^\n]+)", text)
    ids: set[str] = set()
    if block:
        nm, em, ph = block.groups()
        ids = {t for t in (ident(em), ident(ph)) if t}
        if not ident(nm) and re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+", nm.strip()):
            ids.add(_name(nm))
    orders = sorted(set(re.findall(r"\b(ORD-\d+)\b", text)))
    rid = ref.group(2)
    return [Observation(file_id, _stamp(text, "captured") or at, "support_platform", rid, "PRESENT",
                        frozenset(ids), _pii_of(frozenset(ids)), "SUPPORT_ATTACHMENT",
                        "documents.pdf:support_attachment",
                        f"ticket={ref.group(1)}; orders={','.join(orders)}")]


_REG_ROW = re.compile(
    rf"(ORD-\d+) \| (user_\d+) \| ([^|]+?) \| ({_MON}) \| ([A-Z]{{3}} [\d.,]+) \| Retain until ({_MON})")


def _register(text: str, file_id: str, at: str) -> list[Observation]:
    at = _stamp(text, "generated") or at
    out = []
    for order, user, kind, day, amount, until in _REG_ROW.findall(text):
        ids = idents(user)
        out.append(Observation(file_id, at, "legal_service", order, "PRESENT", ids, _pii_of(ids),
                               "LEGAL_HOLD_RECORD", "documents.pdf:legal_register",
                               f"retain_until={_date(until)}; type={kind}; invoice_date={_date(day)}; amount={amount}"))
    return out


def _journal(text: str, file_id: str, at: str) -> list[Observation]:
    rid = re.search(r"Event reference (audit_record_\d+)", text)
    user = re.search(r"subject reference (user_\d+)", text)
    if not rid:
        return []
    ids = idents(user and user.group(1))
    note = "; ".join(f"{k}={v}" for k, v in (("recorded_at", _stamp(text, "recorded")),
                                             ("retain_until", _stamp(text, "retain until"))) if v)
    return [Observation(file_id, _stamp(text, "issued") or at, "security_service", rid.group(1), "PRESENT",
                        ids, _pii_of(ids), "SECURITY_AUDIT_LOG", "documents.pdf:compliance_journal", note)]


PDF_KINDS = {
    "CUSTOMER STATEMENT": _statement,
    "DELIVERY ADDRESS CORRECTION REQUEST": _attachment,
    "DISPUTED ORDERS AND TAX RECORDS REGISTER": _register,
    "COMPLIANCE JOURNAL": _journal,
}


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def parse_pdf(path: Path, file_id: str, captured_at: str) -> list[Observation]:
    text = pdf_text(path)
    title = text.split("\n", 1)[0]
    for key, fn in PDF_KINDS.items():
        if key in title:
            return fn(text, file_id, iso(captured_at))
    return []


def parse(path: Path, file_id: str, captured_at: str) -> tuple[list[Observation], list[Coverage]]:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return parse_txt(path, file_id, captured_at), []
    if suffix == ".pdf":
        return parse_pdf(path, file_id, captured_at), []
    raise ValueError(f"documents parser cannot handle {suffix}")


if __name__ == "__main__":
    import csv

    data = Path(__file__).resolve().parents[3] / "dataset"
    obs: list[Observation] = []
    with open(data / "evidence_files.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            p = data / row["file_path"]
            if p.suffix.lower() in (".txt", ".pdf"):
                o, _ = parse(p, row["file_id"], row["captured_at"])
                obs += o
    for o in obs:
        print(o.file_id, o.observed_at, o.system_id or "-", o.resource_id, o.state, o.category,
              sorted(o.identifiers), sorted(o.pii), o.note)

    by = {(o.file_id, o.resource_id): o for o in obs}
    gold = {
        ("evidence_03", "search_052"): ("search_service", "PRESENT"),
        ("evidence_03", "analytics_2034"): ("analytics_platform", "IDENTIFIABLE"),
        ("evidence_03", "support_864"): ("support_platform", "IDENTIFIABLE"),
        ("evidence_03", "data_flow_9522"): ("integration_service", "ACTIVE"),
        ("evidence_03", "account_4248"): ("account_service", "ABSENT"),
        ("evidence_03", "audit_record_9394"): ("security_service", "PRESENT"),
        ("evidence_03", "backup_3870"): ("backup_service", "RESTORABLE"),
        ("evidence_58", "support_001"): ("support_platform", "PRESENT"),
        ("evidence_55", "audit_record_003"): ("security_service", "PRESENT"),
    }
    for k, (sysid, state) in gold.items():
        o = by[k]
        assert (o.system_id, o.state) == (sysid, state), (k, o)
    assert by[("evidence_03", "support_864")].category == "SUPPORT_MESSAGE"
    assert "user:user_32" in by[("evidence_03", "audit_record_9394")].identifiers
    assert "email:novak.tomasz@icloud.com" in by[("evidence_03", "search_052")].identifiers
    assert "email:choi.jihoon94@gmail.com" in by[("evidence_03", "account_1810")].identifiers
    assert "phone:919876573142" in by[("evidence_58", "support_001")].identifiers
    assert "retain_until=2027-08-25T04:03:00Z" in by[("evidence_55", "audit_record_003")].note
    print(f"\n{len(obs)} observations; gold checks OK")
