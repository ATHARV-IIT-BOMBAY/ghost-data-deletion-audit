import unittest

from ghostaudit import intent

DELETION = "Please permanently delete the account associated with this email address."
MARKETING_ONLY = "Please remove me from marketing lists only. I still want receipts for this account."


class IntentRulesTest(unittest.TestCase):
    def test_explicit_non_deletion_wording(self):
        self.assertFalse(intent.rule_says(MARKETING_ONLY)[0])
        self.assertFalse(intent.rule_says("Please stop the promotional emails. I am not asking for deletion.")[0])

    def test_plain_deletion_request(self):
        self.assertTrue(intent.rule_says(DELETION)[0])


class IntentModelGateTest(unittest.TestCase):
    """The model may flip a request to non-deletion only at MODEL_MIN_CONFIDENCE or above."""

    def _classifier(self, text: str, verdict: bool, confidence: float) -> intent.IntentClassifier:
        clf = intent.IntentClassifier(use_model=True)
        clf.cache = {intent._key(text): {"authorizes_deletion": verdict, "confidence": confidence, "reason": "stub"}}
        return clf

    def test_low_confidence_does_not_flip(self):
        text = "Quiero cerrar algo, no estoy seguro."
        self.assertTrue(self._classifier(text, False, 0.7).authorizes_deletion(text)[0])

    def test_high_confidence_flips(self):
        text = "No borren mi cuenta, solo cambien mi teléfono."
        self.assertFalse(self._classifier(text, False, 0.9).authorizes_deletion(text)[0])

    def test_rules_win_over_model(self):
        self.assertFalse(self._classifier(MARKETING_ONLY, True, 1.0).authorizes_deletion(MARKETING_ONLY)[0])

    def test_no_model_answer_falls_back_to_rules(self):
        clf = intent.IntentClassifier(use_model=True)
        clf.cache = {}
        self.assertTrue(clf.authorizes_deletion(DELETION)[0])


if __name__ == "__main__":
    unittest.main()
