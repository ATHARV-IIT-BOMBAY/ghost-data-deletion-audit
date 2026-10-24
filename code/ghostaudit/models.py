"""Shared data types and identifier normalization.

Every evidence parser turns its file into `Observation` rows (one per resource sighting) and,
where the file is a complete listing of a collection, `Coverage` rows. A coverage row is what
lets the engine prove absence: a resource missing from a full listing captured at T is absent at T.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Observed states a parser may emit. State.py maps them to the policy vocabulary.
OBS_STATES = (
    "PRESENT",       # record exists (no claim about PII)
    "ABSENT",        # explicit not-found: 404, rows=0, NOT_FOUND, result=miss
    "IDENTIFIABLE",  # record exists and still carries direct identifiers of a person
    "REDACTED",      # record exists, direct identifiers removed (stable user_id may remain)
    "ANONYMIZED",    # record exists, cannot be linked to a person
    "ACTIVE",        # data flow / connector enabled
    "DISABLED",      # data flow / connector disabled
    "RESTORABLE",    # backup can still be restored
    "BLOCKED",       # backup restore blocked
)

# Direct-identifier field kinds a record can still carry.
PII_KINDS = ("name", "email", "phone", "email_hash", "user_id")


@dataclass(frozen=True)
class Observation:
    file_id: str                   # evidence_NN from evidence_files.csv
    observed_at: str               # ISO-8601 Z; per-record time if the file has one, else captured_at
    system_id: str                 # e.g. "account_service" (see POLICY.md §6)
    resource_id: str               # e.g. "account_4248"
    state: str                     # one of OBS_STATES
    identifiers: frozenset[str] = frozenset()  # normalized, typed: "email:..", "phone:..", "hash:..", "user:user_12", "name:.."
    pii: frozenset[str] = frozenset()          # subset of PII_KINDS that are non-null on the record
    category: str = ""             # data_category if the file states it (USER_PROFILE, SUPPORT_ATTACHMENT, ...)
    source: str = ""               # parser + table/collection/event, for audit trails
    note: str = ""                 # free text detail (replica name, retention_until, ...)


@dataclass(frozen=True)
class Coverage:
    """A complete listing of `system_id` resources (optionally one collection/table) at a time."""
    file_id: str
    observed_at: str
    system_id: str
    scope: str                                  # table/collection name
    resource_ids: frozenset[str] = field(default_factory=frozenset)


RESOURCE_PREFIX_SYSTEM = {
    "account": "account_service",
    "search": "search_service",
    "cache": "cache_service",
    "support": "support_platform",
    "object": "support_platform",          # support attachments in object storage
    "analytics": "analytics_platform",
    "export": "export_service",
    "marketing": "marketing_service",
    "email_delivery": "email_delivery_service",
    "audit_record": "security_service",
    "legal_hold": "legal_service",
    "backup": "backup_service",
    "tombstone": "identity_service",
    "data_flow": "integration_service",
}


def system_for(resource_id: str) -> str:
    """Default system_id for a resource id when the evidence row doesn't name one."""
    return RESOURCE_PREFIX_SYSTEM.get(resource_id.rsplit("_", 1)[0], "")


_DIGITS = re.compile(r"\D+")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_USER = re.compile(r"^user_\d+$")


def norm_email(v: str) -> str:
    return v.strip().lower()


def norm_phone(v: str) -> str:
    return _DIGITS.sub("", v)


def ident(value: object) -> str | None:
    """Classify and normalize one raw identifier value into a typed token, or None."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    low = v.lower()
    if _EMAIL.match(low):
        return "email:" + low
    if _HASH.match(low):
        return "hash:" + low
    if _USER.match(low):
        return "user:" + low
    digits = norm_phone(v)
    if v.startswith("+") and len(digits) >= 7:
        return "phone:" + digits
    return None


def idents(*values: object) -> frozenset[str]:
    return frozenset(t for t in map(ident, values) if t)


def iso(ts: str) -> str:
    """Normalize timestamps to 'YYYY-MM-DDTHH:MM:SSZ' so string comparison is time comparison."""
    t = ts.strip().replace(" ", "T")
    if t.endswith("+00:00"):
        t = t[:-6] + "Z"
    if len(t) == 16 and t[10] == "T":  # minute precision
        t += ":00Z"
    return t if t.endswith("Z") else t + "Z"
