import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("guest_profile", Path(__file__).resolve().parents[1] / "profile-existing-guest.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ProfileReportTest(unittest.TestCase):
    def test_successful_empty_record_has_no_attribution(self):
        # A successful record/report exit alone was insufficient on the device.
        self.assertEqual(profile.report_counts("Event: task-clock:u\nSamples: 0\nEvent count: 0\n"),
                         {"samples": 0, "reported_sample_event_count": 0, "nonzero_sample_data": False})

    def test_both_samples_and_periods_must_be_nonzero(self):
        self.assertTrue(profile.report_counts("Samples: 17\nEvent count: 1,234\n")["nonzero_sample_data"])
        self.assertFalse(profile.report_counts("Samples: 17\nEvent count: 0\n")["nonzero_sample_data"])

    def test_missing_or_ambiguous_event_counts_are_not_a_profile(self):
        for text in ["", "Samples: 1\n", "Samples: 1\nEvent count: 9\nSamples: 2\nEvent count: 8\n"]:
            with self.assertRaises(ValueError):
                profile.report_counts(text)


if __name__ == "__main__":
    unittest.main()
