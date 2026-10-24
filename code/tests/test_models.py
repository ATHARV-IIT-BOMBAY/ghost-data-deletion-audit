import unittest

from ghostaudit.models import ident, iso, system_for


class NormalizationTest(unittest.TestCase):
    def test_ident_types(self):
        self.assertEqual(ident("  CHOI.JIHOON94@GMAIL.COM "), "email:choi.jihoon94@gmail.com")
        self.assertEqual(ident("+44 7700 900123"), "phone:447700900123")
        self.assertEqual(ident("user_12"), "user:user_12")
        self.assertEqual(ident("A" * 64), "hash:" + "a" * 64)
        self.assertIsNone(ident("no direct identifier"))
        self.assertIsNone(ident(None))

    def test_iso_normalizes_to_comparable_utc(self):
        self.assertEqual(iso("2026-09-15 11:00:00+00:00"), "2026-09-15T11:00:00Z")
        self.assertEqual(iso("2026-09-15T11:00"), "2026-09-15T11:00:00Z")
        self.assertEqual(iso("2026-09-15T11:00:00Z"), "2026-09-15T11:00:00Z")

    def test_system_for_prefix(self):
        self.assertEqual(system_for("audit_record_9394"), "security_service")
        self.assertEqual(system_for("data_flow_31"), "integration_service")


if __name__ == "__main__":
    unittest.main()
