"""Subject resolution: request email → user_id → every current and historical identifier (POLICY.md §4)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .models import ident, norm_email


@dataclass(frozen=True)
class Subject:
    user_id: str
    name: str
    identifiers: frozenset[str]   # typed tokens ("email:..", "phone:..", "hash:..", "user:..")
    matched_on: str               # "current" | "historical"


class IdentityIndex:
    def __init__(self, dataset: Path):
        with open(dataset / "user_identities.csv", newline="", encoding="utf-8") as f:
            self.users = {r["user_id"]: r for r in csv.DictReader(f)}
        with open(dataset / "user_audit_log.csv", newline="", encoding="utf-8") as f:
            self.audit = list(csv.DictReader(f))
        self.current_email = {norm_email(u["email"]): uid for uid, u in self.users.items()}
        self.historical_email: dict[str, str] = {}
        self.history: dict[str, set[str]] = {}
        for a in self.audit:
            for v in (a["old_value"], a["new_value"]):
                tok = ident(v)
                if tok:
                    self.history.setdefault(a["user_id"], set()).add(tok)
            if a["field_name"] == "email":
                self.historical_email.setdefault(norm_email(a["old_value"]), a["user_id"])
        # token → owning user, used to reject resources that clearly belong to someone else
        self.owner: dict[str, str] = {}
        for uid in self.users:
            for tok in self.identifiers(uid):
                self.owner.setdefault(tok, uid)

    def identifiers(self, user_id: str) -> frozenset[str]:
        u = self.users[user_id]
        toks = {t for t in (ident(u["email"]), ident(u["phone"]), ident(u["email_hash"])) if t}
        toks.add("user:" + user_id.lower())
        return frozenset(toks | self.history.get(user_id, set()))

    def resolve(self, email: str) -> Subject | None:
        e = norm_email(email)
        if e in self.current_email:
            uid, how = self.current_email[e], "current"
        elif e in self.historical_email:
            uid, how = self.historical_email[e], "historical"
        else:
            return None
        return Subject(uid, self.users[uid]["name"], self.identifiers(uid), how)
