import random
import unittest

from ghostaudit.schema import canonicalize, make_step, validate_row
from tests._fixtures import gold


class SchemaTest(unittest.TestCase):
    def test_all_gold_rows_are_valid(self):
        for cid, (status, steps) in gold().items():
            self.assertEqual(validate_row(status, steps), [], cid)

    def test_canonicalize_restores_gold_order(self):
        rng = random.Random(7)
        for cid, (_status, steps) in gold().items():
            shuffled = [dict(s) for s in steps]
            rng.shuffle(shuffled)
            self.assertEqual(canonicalize(shuffled), steps, cid)

    def test_not_applicable_must_be_empty(self):
        step = make_step("VERIFY_RECORD_ABSENT", "account_service", "account_1", "evidence_01")
        self.assertTrue(validate_row("NOT_APPLICABLE", [{**step, "step_no": 1}]))
        self.assertEqual(validate_row("NOT_APPLICABLE", []), [])

    def test_complete_verification_must_cite_evidence(self):
        step = {**make_step("VERIFY_RECORD_ABSENT", "account_service", "account_1"), "step_no": 1}
        self.assertTrue(any("must cite evidence" in e for e in validate_row("COMPLETE", [step])))

    def test_unknown_operation_rejected(self):
        step = {**make_step("VERIFY_RECORD_ABSENT", "account_service", "account_1", "evidence_01"),
                "step_no": 1, "operation": "PURGE_EVERYTHING"}
        self.assertTrue(validate_row("COMPLETE", [step]))


if __name__ == "__main__":
    unittest.main()
