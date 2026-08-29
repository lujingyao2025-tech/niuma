import os
import tempfile
import unittest
from unittest import mock

from ophelia_assistant import trial
from ophelia_assistant.config import (
    MAX_CONCURRENT_TASKS,
    MAX_WINDOW_SEQUENCE,
    is_newer_version,
    normalize_window_sequence,
)


class ConfigRulesTests(unittest.TestCase):
    def test_window_sequence_accepts_thirty(self):
        values = list(range(1, 31))
        self.assertEqual(normalize_window_sequence(values), values)
        self.assertEqual(MAX_WINDOW_SEQUENCE, 30)
        self.assertEqual(MAX_CONCURRENT_TASKS, 30)

    def test_window_sequence_rejects_thirty_one(self):
        with self.assertRaises(ValueError):
            normalize_window_sequence(list(range(1, 32)))

    def test_window_sequence_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            normalize_window_sequence([1, 2, 2])

    def test_version_comparison_is_semantic(self):
        self.assertTrue(is_newer_version("0.10.0", "0.9.0"))
        self.assertTrue(is_newer_version("v1.0.0", "0.90.0"))
        self.assertFalse(is_newer_version("0.9.0", "0.10.0"))
        self.assertFalse(is_newer_version("not-a-version", "0.90.0"))

    def test_free_trial_is_three_days(self):
        previous_machine = os.environ.get("NIUMA_MAIL_MACHINE_ID")
        previous_dir = os.environ.get("NIUMA_MAIL_TRIAL_DIR")
        previous_registry = os.environ.get("NIUMA_MAIL_DISABLE_REGISTRY")
        try:
            os.environ["NIUMA_MAIL_MACHINE_ID"] = "source-v090-test"
            os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = "1"
            with tempfile.TemporaryDirectory() as directory:
                os.environ["NIUMA_MAIL_TRIAL_DIR"] = directory
                with mock.patch.object(trial, "_MACHINE_SOURCE_CACHE", None):
                    with mock.patch("ophelia_assistant.trial._read_registry", return_value=None):
                        now = 1_800_000_000
                        status = trial.check_trial(now=now)
                        self.assertTrue(status.active)
                        self.assertEqual(status.expires_at, now + 3 * 24 * 60 * 60)
        finally:
            if previous_machine is None:
                os.environ.pop("NIUMA_MAIL_MACHINE_ID", None)
            else:
                os.environ["NIUMA_MAIL_MACHINE_ID"] = previous_machine
            if previous_dir is None:
                os.environ.pop("NIUMA_MAIL_TRIAL_DIR", None)
            else:
                os.environ["NIUMA_MAIL_TRIAL_DIR"] = previous_dir
            if previous_registry is None:
                os.environ.pop("NIUMA_MAIL_DISABLE_REGISTRY", None)
            else:
                os.environ["NIUMA_MAIL_DISABLE_REGISTRY"] = previous_registry


if __name__ == "__main__":
    unittest.main()
