"""Synthetic guards and tiny owned subprocesses; never loads a guest library."""
import argparse
import json
from pathlib import Path
import plistlib
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import install_acceptance as controller
from app_services.bridge import ServiceJob


class AcceptanceGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.socket_path = self.root / "mux.sock"
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.listener.close)
        library = self.root / "never-loaded-library"
        library.write_bytes(b"synthetic, not a native library")
        self.ipa = self.root / "synthetic.ipa"
        info = {"CFBundleIdentifier": controller.BUNDLE, "CFBundleExecutable": "Probe",
                "MinimumOSVersion": "3.1.3", "UIDeviceFamily": [1]}
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr("Payload/Probe.app/Info.plist", plistlib.dumps(info))
            archive.writestr("Payload/Probe.app/Probe", struct.pack("<7I", 0xfeedface, 12, 6, 2, 0, 0, 0))
        self.args = argparse.Namespace(linux_source=HERE.parents[1], socket=self.socket_path,
            library=library, plist_library=library, evidence=self.root / "evidence", action="install",
            ipa=self.ipa, expected_ipa_sha256=controller.sha(self.ipa),
            product="iPod2,1", ios="3.1.3", arch="armv6")
        self.receipt = {"helperMayBeStarted": False, "installMayBeSubmitted": False,
                        "iconQualified": False, "launchQualified": False, "touchQualified": False}

    @staticmethod
    def info():
        return {"successful": True, "target": {"product_type": "iPod2,1", "ios": "3.1.3",
                "arch": "armv6", "family": "1"}, "result": {"ready": True}}

    def test_default_plan_has_no_path_or_native_operations(self):
        result = subprocess.run([sys.executable, str(HERE / "install_acceptance.py")],
                                capture_output=True, text=True, check=True, timeout=5)
        value = json.loads(result.stdout)
        self.assertEqual(value["status"], "PLAN_ONLY")
        self.assertFalse(value["installMayBeSubmitted"])
        self.assertFalse(value["launchQualified"])

    def test_invalid_review_hash_rejected_before_any_helper_or_evidence(self):
        self.args.expected_ipa_sha256 = "0" * 64
        with patch.object(controller, "run_service") as service:
            with self.assertRaisesRegex(ValueError, "differs"):
                controller.execute(self.args, self.receipt)
        service.assert_not_called()
        self.assertFalse(self.args.evidence.exists())

    def test_wrong_actual_guest_prevents_install(self):
        info = self.info()
        info["target"]["ios"] = "4.2.1"
        with patch.object(controller, "run_service", return_value=info) as service:
            value = controller.execute(self.args, self.receipt)
        self.assertEqual(service.call_count, 1)
        self.assertEqual(value["status"], "FAILED_OR_UNCONFIRMED")
        self.assertFalse(value["installMayBeSubmitted"])

    def test_socket_replacement_after_info_prevents_install(self):
        replacement = socket.socket(socket.AF_UNIX)
        self.addCleanup(replacement.close)
        def replied(*args, **kwargs):
            self.socket_path.unlink()
            replacement.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            return self.info()
        with patch.object(controller, "run_service", side_effect=replied) as service:
            value = controller.execute(self.args, self.receipt)
        self.assertEqual(service.call_count, 1)
        self.assertIn("identity changed", value["error"])
        self.assertFalse(value["installMayBeSubmitted"])

    def test_helper_failure_never_qualifies_visual_or_install(self):
        failure = {"successful": False, "target": self.info()["target"],
                   "result": None, "error": "synthetic unconfirmed timeout"}
        with patch.object(controller, "run_service", side_effect=[self.info(), failure]):
            value = controller.execute(self.args, self.receipt)
        self.assertEqual(value["status"], "FAILED_OR_UNCONFIRMED")
        self.assertTrue(value["installMayBeSubmitted"])
        self.assertTrue(all(not value[key] for key in ("iconQualified", "launchQualified", "touchQualified")))

    def test_even_valid_service_result_cannot_qualify_pixels(self):
        install = {"successful": True, "target": self.info()["target"],
                   "result": {"bundle_id": controller.BUNDLE, "installed": True}}
        with patch.object(controller, "run_service", side_effect=[self.info(), install]):
            value = controller.execute(self.args, self.receipt)
        self.assertEqual(value["status"], "INSTALL_SERVICE_VERIFIED_VISUAL_UNQUALIFIED")
        self.assertTrue(all(not value[key] for key in ("iconQualified", "launchQualified", "touchQualified")))

    def test_public_socket_rejected(self):
        self.socket_path.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            controller.private_socket(self.socket_path)

    def test_real_bounded_helper_timeout_is_unconfirmed_and_reaped(self):
        value = controller.run_service(ServiceJob, [sys.executable, "-c", "import time; time.sleep(5)"],
                                       .05, self.root / "bounded-helper.jsonl", True)
        self.assertFalse(value["successful"])
        self.assertNotEqual(value["exit"], 0)
        self.assertIn("unconfirmed", value["error"])


if __name__ == "__main__":
    unittest.main()
