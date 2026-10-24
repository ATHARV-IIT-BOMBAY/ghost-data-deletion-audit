"""Zero-model audit engine: subject → linked resources → state at audit_at → policy → ordered steps.

Every decision here is deterministic and traceable to an evidence row (POLICY.md §4–§8).
The model layers (intent classifier, media reader, investigator) plug in on top of this.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import intent
from .activity import explicit_links, load_activity
from .identity import IdentityIndex, Subject
from .ingest import load_evidence
from .models import Coverage, Observation, iso, system_for
from .policy import TREATMENTS, Policy
from .schema import Step, canonicalize, make_step

# C5: when two observations share a timestamp, the one showing data still exposed wins.
_EXPOSURE_RANK = {"IDENTIFIABLE": 5, "PRESENT": 4, "ACTIVE": 4, "RESTORABLE": 4,
                  "REDACTED": 2, "ANONYMIZED": 2, "DISABLED": 1, "BLOCKED": 1, "ABSENT": 0}


_REPLICA = re.compile(r"(read_replica|failover_replica|reporting_replica|replica_[a-z]+_[a-z]+)")


def location(o: Observation) -> str:
    """Which copy of the data an observation describes; replicas drift independently of primary."""
    explicit = re.search(r"\breplica=(\w+)", o.note)  # rows say which copy they are; trust that first
    if explicit:
        name = explicit.group(1)
        return "primary" if name == "primary" else name
    text = o.note + " " + o.source
    if "_primary" in text:  # e.g. coverage scope "customer_data_primary.customer_profiles"
        return "primary"
    m = _REPLICA.search(text)
    return m.group(1) if m else "primary"


# (resource family, event type, minutes before reported_at) for a case's own workflow events.
# Measured on the 32 samples: primary-profile deletions land -8..+4 ("operation completed" style)
# or 45..75 min before reported_at; analytics "Policy action completed" anonymizations 28..29
# (6 for the completed style). Events outside these windows belong to other cases or are retries.
# Columns: family, event type, minutes before reported_at, one event per case?, filter by status?
# Every deletion case has exactly one primary account, so "one candidate left → it is that case's"
# is sound for accounts. Many cases have no anonymized analytics record, so analytics only use
# the event-side rule (an event with exactly one possible case).
WORKFLOW_EVENTS = (
    ("account", "DELETE_RECORD", (-10.0, 80.0), True, True),
    ("analytics", "ANONYMIZE_RECORD", (0.0, 35.0), False, False),
)

# A deletion workflow's reported_status is the claim its own account-deletion step produced.
# Measured on all 37 sample account events: COMPLETE↔SUCCESS 20/20, NOT_STARTED↔FAILED 12/12,
# PARTIALLY_COMPLETE↔PARTIAL|SUCCESS 5/5. Used only to rule candidates out, never to decide state.
STATUS_RESULTS = {"COMPLETE": {"SUCCESS"}, "NOT_STARTED": {"FAILED"},
                  "PARTIALLY_COMPLETE": {"PARTIAL", "SUCCESS"}}


def _minutes(event_time: str, reported_at: str) -> float:
    def ts(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (ts(reported_at) - ts(event_time)).total_seconds() / 60


def _rank(o: Observation) -> tuple:
    return (o.observed_at, _EXPOSURE_RANK.get(o.state, 3))


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list[Step]
    trace: list[str] = field(default_factory=list)


class Engine:
    def __init__(self, dataset: Path, use_models: bool = True):
        self.dataset = dataset
        self.intent = intent.IntentClassifier(use_models)
        self.identity = IdentityIndex(dataset)
        self.policy = Policy(dataset)
        self.activity = load_activity(dataset)
        with open(dataset / "evidence_files.csv", newline="", encoding="utf-8") as f:
            manifest = list(csv.DictReader(f))
        self.captured = {r["file_id"]: iso(r["captured_at"]) for r in manifest}
        self.paths = {r["file_id"]: r["file_path"] for r in manifest}
        obs, self.coverage, self.ingest_problems = load_evidence(dataset)
        self.by_resource: dict[str, list[Observation]] = defaultdict(list)
        self.by_token: dict[str, set[str]] = defaultdict(set)
        for o in obs:
            self.by_resource[o.resource_id].append(o)
            for tok in o.identifiers:
                if not tok.startswith("name:"):
                    self.by_token[tok].add(o.resource_id)
        self.links = explicit_links(self.activity)
        # JSON snapshot bundles are captured per subject group, not per system: an empty or
        # partial collection there proves absence only for the people that file is about.
        self.file_subjects: dict[str, frozenset[str]] = {}
        # Same for SQL "inventory" captures (snapshot_scope tables): scoped extracts, not full dumps.
        for fid in {c.file_id for c in self.coverage}:
            path = self.paths.get(fid, "")
            if path.endswith(".json") or "inventory" in path:
                self.file_subjects[fid] = frozenset(t for o in obs if o.file_id == fid
                                                    for t in o.identifiers if not t.startswith("name:"))

    # -- time rule (POLICY.md §5) -------------------------------------------------------------
    def _visible(self, o: Observation, audit_at: str) -> bool:
        return o.observed_at <= audit_at and self.captured.get(o.file_id, o.observed_at) <= audit_at

    # -- linking --------------------------------------------------------------------------------
    def link(self, subject: Subject, requested_at: str, audit_at: str) -> dict[str, tuple[str, bool]]:
        """resource_id → (why it belongs to the subject, linked only by evidence after audit_at).

        Ownership is a fact about the record, not about the audit moment, so later evidence may
        link a resource; it can never decide that resource's state (POLICY.md §5).
        A not-found lookup proves ownership only when it was part of this request's handling.
        """
        found: dict[str, tuple[str, bool]] = {}
        for tok in sorted(subject.identifiers):
            for rid in self.by_token.get(tok, ()):
                hits = [o for o in self.by_resource[rid] if tok in o.identifiers
                        and (o.state != "ABSENT" or o.observed_at >= requested_at)]
                if hits:
                    late = all(not self._visible(o, audit_at) for o in hits)
                    if rid not in found or found[rid][1] and not late:
                        found[rid] = (f"identifier {tok}", late)
        for a, rid, toks, _sole in self.links:
            if toks & subject.identifiers and rid not in found:
                found[rid] = (f"catalog link {a.activity_id}", a.event_time > audit_at)
        return found

    # -- workflow linking (accounts that no evidence row names) ----------------------------------
    def assign_workflow_accounts(self, requests: list[dict]) -> None:
        """Match deletion-worker primary-profile events to cases, globally, by elimination.

        Measured on the 32 samples: a case's own account event lands WORKFLOW_WINDOW_MIN before
        its reported_at, but several cases often share a reported_at, so timing alone is
        ambiguous. Events whose account is already identifier-linked go to that case; then any
        case with one remaining candidate (or event with one remaining case) is fixed, repeatedly.
        Ties that never resolve stay unassigned rather than guessed. Account candidates must also
        have a result compatible with the case's reported_status (STATUS_RESULTS); analytics skip
        case-side elimination because not every case has an anonymized analytics record.
        """
        live: list[tuple[dict, Subject]] = []
        for r in requests:
            subject = self.identity.resolve(r["email"])
            if subject is not None and self.intent.authorizes_deletion(r["request_text"])[0]:
                live.append((r, subject))
        linked = {r["case_id"]: self.link(s, iso(r["requested_at"]), iso(r["audit_at"])) for r, s in live}
        self.workflow_links: dict[str, list[tuple[str, str]]] = defaultdict(list)
        stats = []
        for family, event_type, window, one_per_case, by_status in WORKFLOW_EVENTS:
            events = [a for a in self.activity if a.source_system == "deletion_worker"
                      and a.event_type == event_type and a.resource_id.startswith(family + "_")]
            assigned = self._assign(events, window, live, linked, one_per_case, by_status)
            for cid, a in assigned.items():
                self.workflow_links[cid].append((a.resource_id, a.activity_id))
            stats.append(f"{family}:{len(assigned)}")
        self.workflow_stats = (len(live), ", ".join(stats))

    @staticmethod
    def _assign(events, window, live, linked, one_per_case: bool, by_status: bool) -> dict:
        lo, hi = window
        cases: dict[str, set[str]] = {}
        known: dict[str, str] = {}  # rid → case already proven by identifiers
        for r, _subject in live:
            rep = iso(r["reported_at"])
            allowed = STATUS_RESULTS.get(r["reported_status"]) if by_status else None
            cases[r["case_id"]] = {a.activity_id for a in events
                                   if lo <= _minutes(a.event_time, rep) <= hi
                                   and (allowed is None or a.reported_result in allowed)}
            for rid in linked[r["case_id"]]:
                known[rid] = r["case_id"]
        by_id = {a.activity_id: a for a in events}
        assigned: dict[str, str] = {}
        for aid, a in sorted(by_id.items()):
            if a.resource_id in known and known[a.resource_id] in cases:
                assigned[known[a.resource_id]] = aid
        changed = True
        while changed:  # sorted iteration: identical result in every process
            changed = False
            taken = set(assigned.values())
            if one_per_case:
                for cid, cands in sorted(cases.items()):
                    if cid in assigned:
                        continue
                    free = cands - taken
                    if len(free) == 1:
                        assigned[cid] = free.pop()
                        taken.add(assigned[cid])
                        changed = True
            open_cases = {cid: cands - taken for cid, cands in cases.items() if cid not in assigned}
            for aid in sorted({x for c in open_cases.values() for x in c}):
                owners = [cid for cid, c in open_cases.items() if aid in c]
                if len(owners) == 1:
                    assigned[owners[0]] = aid
                    changed = True
                    break
        return {cid: by_id[aid] for cid, aid in assigned.items()}

    # -- state at audit_at ----------------------------------------------------------------------
    def _absence_from_coverage(self, rid: str, audit_at: str, subject: Subject) -> list[Observation]:
        family, system = rid.rsplit("_", 1)[0], system_for(rid)
        out = []
        for c in self.coverage:
            if c.observed_at > audit_at or self.captured.get(c.file_id, c.observed_at) > audit_at:
                continue
            scoped = self.file_subjects.get(c.file_id)
            if scoped is not None and not scoped & subject.identifiers:
                continue
            fams = {r.rsplit("_", 1)[0] for r in c.resource_ids}
            applies = family in fams if fams else c.system_id == system
            if applies and rid not in c.resource_ids:
                out.append(Observation(c.file_id, c.observed_at, c.system_id, rid, "ABSENT",
                                       source=f"coverage:{c.scope}", note=c.scope))
        return out

    def state_at(self, rid: str, audit_at: str, subject: Subject,
                 ok_states: set[str]) -> tuple[Observation | None, list[Observation]]:
        """(latest primary-copy observation, replica copies that drifted) at audit_at — C4/C5.

        A replica counts only when one snapshot shows the primary already compliant while
        the replica still exposes the data; an old replica sighting is just old, not drift.
        """
        cands = [o for o in self.by_resource.get(rid, []) if self._visible(o, audit_at)]
        # Retained records (audit logs) are often created by the deletion itself, so an earlier
        # listing that lacks them proves nothing; only an explicit not-found counts against them.
        if "PRESENT" not in ok_states or "ABSENT" in ok_states:
            cands += self._absence_from_coverage(rid, audit_at, subject)
        primary = [o for o in cands if location(o) == "primary"] or cands
        latest = max(primary, key=_rank) if primary else None
        replica_latest: dict[str, Observation] = {}
        for o in cands:
            loc = location(o)
            if loc != "primary" and (loc not in replica_latest or _rank(o) > _rank(replica_latest[loc])):
                replica_latest[loc] = o
        drifted = [r for r in replica_latest.values()
                   if r.state not in ok_states and r.state != "ABSENT"
                   and any(c.file_id == r.file_id and location(c) == "primary" and c.state in ok_states
                           for c in cands)]
        return latest, drifted

    # -- one case -------------------------------------------------------------------------------
    def audit(self, req: dict) -> CaseResult:
        cid, audit_at = req["case_id"], iso(req["audit_at"])
        res = CaseResult(cid, "INCOMPLETE", [])
        ok, why = self.intent.authorizes_deletion(req["request_text"])
        res.trace.append(f"intent: {why}")
        if not ok:
            res.status = "NOT_APPLICABLE"
            return res
        subject = self.identity.resolve(req["email"])
        if subject is None:
            res.trace.append("subject unresolved")
            return res
        res.trace.append(f"subject {subject.user_id} via {subject.matched_on} email")

        steps: list[Step] = []
        findings = 0
        future_checks: list[Step] = []  # resources with no state evidence by audit_at (switch S4)
        requested_at = iso(req["requested_at"])
        links = self.link(subject, requested_at, audit_at)
        for rid, aid in getattr(self, "workflow_links", {}).get(cid, []):
            links.setdefault(rid, (f"workflow event {aid}", False))
        for rid, (why, late) in sorted(links.items()):
            obs = self.by_resource.get(rid, [])
            req_state = self.policy.required_state(rid, obs)
            if req_state is None:
                res.trace.append(f"{rid}: skipped (no rule)")
                continue
            required, rule_id = req_state
            ok_states, finding, remediation, verification = TREATMENTS[required]
            latest, drifted = self.state_at(rid, audit_at, subject, ok_states)
            if latest is None:
                # Known to belong to the subject, state unknown at audit_at: it can only appear as a
                # future verification check, which exists only when the case is INCOMPLETE.
                future_checks.append(make_step(verification, system_for(rid), rid))
                res.trace.append(f"{rid} [{why}{', late' if late else ''}]: no state evidence by audit_at")
                continue
            bad = drifted + ([latest] if latest.state not in ok_states else [])
            # A sighting from before the request says nothing about whether the deletion worked:
            # every one of the 41 gold findings cites evidence captured after requested_at.
            stale = [o for o in bad if o.observed_at < requested_at]
            if stale and len(stale) == len(bad):
                future_checks.append(make_step(verification, system_for(rid), rid))
                res.trace.append(f"{rid} [{why}]: only pre-request evidence ({stale[0].state}@{stale[0].file_id})")
                continue
            bad = [o for o in bad if o.observed_at >= requested_at]
            current = max(bad, key=_rank) if bad else latest
            system = system_for(rid) or current.system_id
            res.trace.append(f"{rid} [{why}] {latest.state}@{latest.file_id}"
                             + "".join(f" drift:{location(d)}={d.state}@{d.file_id}" for d in drifted)
                             + f" needs {required} ({rule_id})")
            if not bad:
                steps.append(make_step(verification, system, rid, current.file_id))
            elif finding is None:  # a retained record that is missing: nothing we may remediate
                res.trace.append(f"{rid}: retained record not present")
            else:
                findings += 1
                steps += [make_step(finding, system, rid, current.file_id),
                          make_step(remediation, system, rid),
                          make_step(verification, system, rid)]

        if findings:
            res.status = "INCOMPLETE"
            steps += future_checks
            for s in steps:  # future work cites nothing (POLICY.md §8)
                if s["phase"] != "FINDING":
                    s["evidence_id"] = ""
        else:
            res.status = "COMPLETE"
        res.steps = canonicalize(steps)
        return res
