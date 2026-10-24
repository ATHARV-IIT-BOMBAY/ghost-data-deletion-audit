"""Closed vocabularies, canonical step ordering and the output-contract validator.

Single source of truth: the solver, the prompts and the scorer all import from here,
so an allowed value can never be listed in one place and missing in another.
Values are copied verbatim from problem_statement.md ("Allowed Operations").
"""

from __future__ import annotations

import json
from typing import Any, Iterable

STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

# Table order matters: within a phase, steps are sorted by this order (POLICY.md §3).
OPERATIONS: dict[str, tuple[str, str]] = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
OP_RANK = {op: i for i, op in enumerate(OPERATIONS)}
PHASE_RANK = {p: i for i, p in enumerate(PHASES)}
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")
EVIDENCE_PREFIXES = ("evidence_", "activity_", "audit_")

# Switch S1 (POLICY.md §7): the 32 samples never place two same-op, same-system resources
# next to each other, so string vs numeric resource_id order is unobservable. "Ascending"
# on a string column defaults to lexicographic.
RESOURCE_SORT = "string"

Step = dict[str, Any]


def _resource_key(resource_id: str) -> tuple:
    if RESOURCE_SORT == "numeric":
        prefix, _, num = resource_id.rpartition("_")
        return (prefix, int(num) if num.isdigit() else -1, resource_id)
    return (resource_id,)


def sort_key(step: Step) -> tuple:
    return (PHASE_RANK[step["phase"]], OP_RANK[step["operation"]], step["system_id"],
            _resource_key(step["resource_id"]))


def canonicalize(steps: Iterable[Step]) -> list[Step]:
    """Sort into contract order and renumber step_no from 1."""
    ordered = sorted(steps, key=sort_key)
    return [{**s, "step_no": i} for i, s in enumerate(ordered, start=1)]


def make_step(operation: str, system_id: str, resource_id: str, evidence_id: str = "") -> Step:
    phase, state = OPERATIONS[operation]
    return {"step_no": 0, "phase": phase, "system_id": system_id, "resource_id": resource_id,
            "operation": operation, "state": state, "evidence_id": evidence_id}


def dumps_steps(steps: list[Step]) -> str:
    """Deterministic serialization: fixed key order, compact separators."""
    return json.dumps([{k: s[k] for k in STEP_FIELDS} for s in steps],
                      ensure_ascii=False, separators=(",", ":"))


def validate_row(status: str, steps: Any) -> list[str]:
    """Return every contract violation for one output row (empty list = valid)."""
    errs: list[str] = []
    if status not in STATUSES:
        return [f"unknown status {status!r}"]
    if not isinstance(steps, list):
        return ["steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have []"]
    if not steps:
        errs.append(f"{status} with no steps")
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict) or set(s) != set(STEP_FIELDS):
            errs.append(f"step {i}: fields must be exactly {STEP_FIELDS}")
            continue
        if s["step_no"] != i:
            errs.append(f"step {i}: step_no={s['step_no']}")
        if s["operation"] not in OPERATIONS:
            errs.append(f"step {i}: unknown operation {s['operation']!r}")
            continue
        phase, state = OPERATIONS[s["operation"]]
        if (s["phase"], s["state"]) != (phase, state):
            errs.append(f"step {i}: {s['operation']} needs phase={phase} state={state}")
        ev = s["evidence_id"]
        cites = ev != ""
        if cites and not ev.startswith(EVIDENCE_PREFIXES):
            errs.append(f"step {i}: bad evidence_id {ev!r}")
        if status == "COMPLETE":
            if phase != "VERIFICATION":
                errs.append(f"step {i}: COMPLETE allows only VERIFICATION")
            if not cites:
                errs.append(f"step {i}: COMPLETE verification must cite evidence")
        elif phase == "FINDING" and not cites:
            errs.append(f"step {i}: finding must cite evidence")
        elif phase != "FINDING" and cites:
            errs.append(f"step {i}: future {phase.lower()} must use empty evidence_id")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps if isinstance(s, dict)):
        errs.append("INCOMPLETE without findings")
    if not errs and steps != canonicalize(steps):
        errs.append("steps not in canonical order")
    return errs
