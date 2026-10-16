import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lighttouch_desktop.windows_preview import (PreviewImportError, proposal,
                                               read_proposal, windows_absolute)


class PreviewTests(unittest.TestCase):
    root = r"C:\Users\Blake\AppData\Local\LightTouch\Windows-preview"

    def record(self, **changes):
        value = {"Id": "abcde123" * 4, "Name": "My saved iPod", "BasePath": r"D:\iOS\n72",
                 "RomPath": r"D:\iOS\bootrom.bin", "GuestPackagePath": r"D:\iOS\armv6.itpack"}
        return dict(value, **changes)

    def encode(self, records):
        return json.dumps(records).encode()

    def test_preserves_saved_identity_and_original_state(self):
        data = self.encode([self.record()]); result = proposal(data, self.root)
        self.assertEqual(result["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["devices"][0]["id"], self.record()["Id"])
        self.assertEqual(windows_absolute(result["devices"][0]["state"]),
                         windows_absolute(self.root + "\\devices\\" + self.record()["Id"]))
        self.assertTrue(result["requires_explicit_confirmation"])
        self.assertFalse(result["writable_state_copied"])
        self.assertEqual(result["native_admission"], "not performed")
        self.assertEqual(result["lease_name"], "running.lock")

    def test_preserves_path_and_state_id_spelling_for_existing_binding(self):
        record = self.record(Id="ABCDEF01" * 4)
        result = proposal(self.encode([record]), self.root)
        device = result["devices"][0]
        self.assertEqual(device["base"], record["BasePath"])
        self.assertEqual(device["guest_package"], record["GuestPackagePath"])
        self.assertEqual(device["state"], self.root + "\\devices\\" + record["Id"])
        self.assertEqual(device["id"], record["Id"].lower())

    def test_encoding_and_optional_path_type_are_strict(self):
        with self.assertRaises(PreviewImportError):
            proposal(json.dumps([self.record()]).encode("utf-16"), self.root)
        for value in [None, 0, False, []]:
            with self.subTest(value=value), self.assertRaises(PreviewImportError):
                proposal(self.encode([self.record(RomPath=value)]), self.root)

    def test_read_is_nonmutating(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"; before = self.encode([self.record()]); path.write_bytes(before)
            read_proposal(path, self.root)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([p.name for p in Path(temp).iterdir()], ["library.json"])

    def test_unknown_or_missing_fields_rejected(self):
        for record in [self.record(NewUnsupported="x"), {"Id": "a" * 32}]:
            with self.assertRaises(PreviewImportError): proposal(self.encode([record]), self.root)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(PreviewImportError): proposal(b'[{"Id":"a","Id":"b"}]', self.root)

    def test_windows_case_alias_duplicate_identity_rejected(self):
        a = self.record(); b = self.record(Id=a["Id"].upper())
        with self.assertRaises(PreviewImportError): proposal(self.encode([a, b]), self.root)

    def test_state_base_overlap_rejected_case_insensitively(self):
        base = self.root.upper() + "\\devices\\" + self.record()["Id"]
        with self.assertRaises(PreviewImportError): proposal(self.encode([self.record(BasePath=base)]), self.root)

    def test_future_state_under_another_base_rejected(self):
        records = [self.record(BasePath=self.root + "\\devices"), self.record(Id="b" * 32)]
        with self.assertRaises(PreviewImportError): proposal(self.encode(records), self.root)

    def test_relative_drive_relative_and_device_namespace_rejected(self):
        for path in ["relative", r"C:relative", r"\rooted-without-drive", r"\\?\C:\base", r"\\.\PhysicalDrive0"]:
            with self.subTest(path=path), self.assertRaises(PreviewImportError): windows_absolute(path)

    def test_unicode_and_unc_base_paths_supported_as_metadata(self):
        record = self.record(BasePath=r"\\server\share\iOS\é", RomPath="")
        result = proposal(self.encode([record]), self.root)
        self.assertIn("é", result["devices"][0]["base"])
        self.assertEqual(result["devices"][0]["bootrom"], "")

    def test_oversized_malformed_and_wrong_root_rejected(self):
        for data in [b" " * (1024 * 1024 + 1), b"bad-json", b"{}", b"[1]"]:
            with self.assertRaises(PreviewImportError): proposal(data, self.root)


if __name__ == "__main__": unittest.main()
