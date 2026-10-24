import unittest

from ghostaudit.ingest import media


class VisionGuardTest(unittest.TestCase):
    def setUp(self):
        self._known = media.KNOWN_IDS
        media.KNOWN_IDS = {"audit_record_003", "marketing_001"}

    def tearDown(self):
        media.KNOWN_IDS = self._known

    def test_zero_padding_mismatch_is_detected(self):
        self.assertEqual(media._padding_error("audit_record_3"), "audit_record_003")
        self.assertIsNone(media._padding_error("audit_record_004"))

    def test_status_text_mapping(self):
        self.assertEqual(media._state("backup", "Archive", False), "RESTORABLE")
        self.assertEqual(media._state("backup", "Restore blocked", False), "BLOCKED")
        self.assertEqual(media._state("data_flow", "Healthy", False), "ACTIVE")
        self.assertEqual(media._state("data_flow", "Disabled", False), "DISABLED")
        self.assertEqual(media._state("account", "Not found", False), "ABSENT")
        self.assertEqual(media._state("support", "", True), "IDENTIFIABLE")


if __name__ == "__main__":
    unittest.main()
