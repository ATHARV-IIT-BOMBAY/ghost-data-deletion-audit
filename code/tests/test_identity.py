import unittest

from ghostaudit.identity import IdentityIndex
from tests._fixtures import DATASET


class IdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = IdentityIndex(DATASET)

    def test_historical_email_resolves_to_same_user(self):
        current = self.index.resolve("minjun.kim@gmail.com")
        old = self.index.resolve("minjun.kim90@gmail.com")
        self.assertIsNotNone(current)
        self.assertEqual(current.user_id, old.user_id)
        self.assertEqual(old.matched_on, "historical")

    def test_email_match_is_case_insensitive(self):
        self.assertEqual(self.index.resolve("MINJUN.KIM@GMAIL.COM").user_id,
                         self.index.resolve("minjun.kim@gmail.com").user_id)

    def test_unknown_email_is_none(self):
        self.assertIsNone(self.index.resolve("nobody@example.invalid"))

    def test_near_miss_decoy_does_not_resolve(self):
        real = self.index.resolve("g.silva76@gmail.com")
        decoy = self.index.resolve("g.silva76.jr@gmail.com")
        self.assertIsNotNone(real)
        self.assertTrue(decoy is None or decoy.user_id != real.user_id)


if __name__ == "__main__":
    unittest.main()
