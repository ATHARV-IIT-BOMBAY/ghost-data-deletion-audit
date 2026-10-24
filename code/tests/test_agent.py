import csv
import unittest

from ghostaudit.agent import Investigator
from ghostaudit.models import iso
from tests._fixtures import DATASET

LINE = "- 2026-10-01T12:00:00Z | account_service | account_4248 | USER_PROFILE | NOT_FOUND | novak.tomasz@icloud.com"


class InvestigatorVerificationTest(unittest.TestCase):
    """The model proposes; only code-verified quotes become observations (no API calls here)."""

    @classmethod
    def setUpClass(cls):
        with open(DATASET / "evidence_files.csv", newline="", encoding="utf-8") as f:
            captured = {r["file_id"]: iso(r["captured_at"]) for r in csv.DictReader(f)}
        cls.inv = Investigator(DATASET, captured, enabled=False)

    def sub(self, **kw):
        base = {"file_id": "evidence_03", "resource_id": "account_4248", "state": "ABSENT", "quote": LINE}
        return {**base, **kw}

    def test_verbatim_quote_is_accepted(self):
        obs = self.inv._verify(self.sub(), "account_4248", "2026-10-02T00:00:00Z")
        self.assertIsNotNone(obs)
        self.assertEqual((obs.file_id, obs.state), ("evidence_03", "ABSENT"))

    def test_invented_quote_is_rejected(self):
        bad = LINE.replace("NOT_FOUND", "FOUND")
        self.assertIsNone(self.inv._verify(self.sub(quote=bad, state="PRESENT"), "account_4248", "2026-10-02T00:00:00Z"))

    def test_file_after_audit_at_is_rejected(self):
        self.assertIsNone(self.inv._verify(self.sub(), "account_4248", "2026-09-30T00:00:00Z"))

    def test_quote_about_another_record_is_rejected(self):
        self.assertIsNone(self.inv._verify(self.sub(resource_id="account_146"), "account_146", "2026-10-02T00:00:00Z"))

    def test_state_without_supporting_wording_is_rejected(self):
        self.assertIsNone(self.inv._verify(self.sub(state="BLOCKED"), "account_4248", "2026-10-02T00:00:00Z"))

    def test_search_hides_files_after_audit_at(self):
        self.assertEqual(self.inv._search("account_4248", "2026-09-30T00:00:00Z")
                         and [h for h in self.inv._search("account_4248", "2026-09-30T00:00:00Z")
                              if h["file_id"] == "evidence_03"], [])


if __name__ == "__main__":
    unittest.main()
