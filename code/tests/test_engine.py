import unittest

from ghostaudit.activity import Activity
from ghostaudit.engine import Engine
from tests._fixtures import DATASET, all_requests, gold


def _event(aid: str, rid: str, result: str, time: str) -> Activity:
    return Activity(aid, time, "DELETE_RECORD", "deletion_worker", "account_service", rid, result,
                    f"Deletion worker submitted primary profile {rid} to account_service.")


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(DATASET, use_models=False)  # rules-only intent, cached vision
        cls.requests = all_requests()
        cls.engine.assign_workflow_accounts(list(cls.requests.values()))
        cls.gold = gold()

    def test_case_11_matches_gold(self):
        res = self.engine.audit(self.requests["case_11"])
        self.assertEqual((res.status, res.steps), self.gold["case_11"])

    def test_no_evidence_after_audit_at(self):
        import csv
        with open(DATASET / "evidence_files.csv", newline="", encoding="utf-8") as f:
            captured = {r["file_id"]: r["captured_at"] for r in csv.DictReader(f)}
        req = {**self.requests["case_11"], "audit_at": "2026-09-30T12:00:00Z"}
        for s in self.engine.audit(req).steps:
            if s["evidence_id"].startswith("evidence_"):
                self.assertLessEqual(captured[s["evidence_id"]], req["audit_at"], s)

    def test_status_filter_breaks_a_timing_tie(self):
        """Two cases, same reported_at, two equally-timed events: results decide the owner."""
        same = "2026-09-06T12:00:00Z"
        live = [({"case_id": "A", "reported_at": same, "reported_status": "COMPLETE"}, None),
                ({"case_id": "B", "reported_at": same, "reported_status": "NOT_STARTED"}, None)]
        events = [_event("activity_1", "account_1", "FAILED", "2026-09-06T11:00:00Z"),
                  _event("activity_2", "account_2", "SUCCESS", "2026-09-06T11:00:00Z")]
        got = Engine._assign(events, (-10.0, 80.0), live, {"A": {}, "B": {}}, True, True)
        self.assertEqual({c: a.resource_id for c, a in got.items()}, {"A": "account_2", "B": "account_1"})

    def test_incompatible_result_is_never_assigned(self):
        """Only the status filter separates these: without it the FAILED event (sorted first) wins."""
        live = [({"case_id": "A", "reported_at": "2026-09-06T12:00:00Z", "reported_status": "COMPLETE"}, None)]
        events = [_event("activity_1", "account_1", "FAILED", "2026-09-06T11:00:00Z"),
                  _event("activity_2", "account_2", "SUCCESS", "2026-09-06T11:10:00Z")]
        got = Engine._assign(events, (-10.0, 80.0), live, {"A": {}}, True, True)
        self.assertEqual(got["A"].resource_id, "account_2")

    def test_unresolvable_tie_stays_unassigned(self):
        same = "2026-09-06T12:00:00Z"
        live = [({"case_id": "A", "reported_at": same, "reported_status": "COMPLETE"}, None),
                ({"case_id": "B", "reported_at": same, "reported_status": "COMPLETE"}, None)]
        events = [_event("activity_1", "account_1", "SUCCESS", "2026-09-06T11:00:00Z"),
                  _event("activity_2", "account_2", "SUCCESS", "2026-09-06T11:00:00Z")]
        self.assertEqual(Engine._assign(events, (-10.0, 80.0), live, {"A": {}, "B": {}}, True, True), {})


if __name__ == "__main__":
    unittest.main()
