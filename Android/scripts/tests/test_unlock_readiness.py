"""Small host JVM checks of the actual Android-test readiness admission class."""
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
POLICY = HERE.parents[1] / 'app/src/androidTest/java/org/lighttouch/android/GuestUnlockReadiness.java'


class UnlockReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        subprocess.run(['javac', '-J-Xmx128m', '-d', cls.temp.name, str(POLICY),
                        str(HERE / 'unlock_readiness.java')], check=True, timeout=20)

    def test_actual_admission_and_continuation_contract(self):
        for scenario in ['poll-window', 'sparse', 'banner-reflow', 'sleeping-ui', 'unusable',
                         'identity', 'input-generation', 'surface-generation', 'width',
                         'height', 'invalid', 'deadline', 'clock']:
            with self.subTest(scenario=scenario):
                completed = subprocess.run(['java', '-Xmx64m', '-cp', self.temp.name,
                    'org.lighttouch.android.UnlockReadinessHostTest', scenario],
                    check=True, text=True, capture_output=True, timeout=5)
                self.assertEqual(completed.stdout.strip(), 'PASS ' + scenario)


if __name__ == '__main__':
    unittest.main()
