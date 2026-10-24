"""deletion_rules.csv → the final state each resource must reach (POLICY.md §6–§7)."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Observation

# Required final state → (state(s) that already satisfy it, finding, remediation, verification)
TREATMENTS = {
    "ABSENT": ({"ABSENT"}, "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACTED": ({"REDACTED", "ANONYMIZED"}, "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZED": ({"ANONYMIZED"}, "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "PRESENT": ({"PRESENT", "IDENTIFIABLE", "REDACTED", "ANONYMIZED"}, None, None, "VERIFY_RECORD_PRESENT"),
    "DISABLED": ({"DISABLED"}, "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCKED": ({"BLOCKED"}, "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}

# Resource family → rule_id in deletion_rules.csv (category resolved per observation where needed).
FAMILY_RULE = {
    "account": "rule_01", "search": "rule_03", "cache": "rule_04", "analytics": "rule_07",
    "export": "rule_09", "marketing": "rule_10", "email_delivery": "rule_11", "audit_record": "rule_12",
    "legal_hold": "rule_13", "backup": "rule_14", "tombstone": "rule_15",
}
ACTION_STATE = {"DELETE": "ABSENT", "REDACT": "REDACTED", "ANONYMIZE": "ANONYMIZED", "RETAIN": "PRESENT"}


class Policy:
    def __init__(self, dataset: Path):
        with open(dataset / "deletion_rules.csv", newline="", encoding="utf-8") as f:
            self.rules = {r["rule_id"]: r for r in csv.DictReader(f)}

    def rule_for(self, resource_id: str, obs: list[Observation]) -> str | None:
        family = resource_id.rsplit("_", 1)[0]
        cats = {o.category for o in obs}
        # Contact indexes (legacy support contact index, opted-in marketing contact lookups) are
        # copies of identifiers, not profiles: delete them whatever family their id belongs to.
        if "SECONDARY_IDENTIFIER" in cats or any("contact_" in o.source for o in obs):
            return "rule_02"
        if "MARKETING_PROFILE" in cats:
            return "rule_10"
        if family in ("support", "object"):
            cats = {o.category for o in obs}
            return "rule_06" if "SUPPORT_ATTACHMENT" in cats or family == "object" else "rule_05"
        return FAMILY_RULE.get(family)

    def required_state(self, resource_id: str, obs: list[Observation]) -> tuple[str, str] | None:
        """(required final state, rule_id) — data flows have no rule row: they must be DISABLED."""
        family = resource_id.rsplit("_", 1)[0]
        if family == "data_flow":
            return "DISABLED", "recreation"
        rule_id = self.rule_for(resource_id, obs)
        if rule_id is None:
            return None
        if rule_id == "rule_14":  # backup archives are retained, but restore must be blocked
            return "BLOCKED", rule_id
        return ACTION_STATE[self.rules[rule_id]["required_action"]], rule_id
