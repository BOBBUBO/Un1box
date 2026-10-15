import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "prepared base"
        self.base.mkdir()
        (self.base / "device.lock.json").write_text(json.dumps({"format": 1, "board": "n81ap", "build": "8C148", "boot_strategy": "kboot"}))
        self.base.chmod(0o500)
        self.addCleanup(lambda: self.base.chmod(0o700))
        self.package = self.root / "armv7.itpack"
        self.package.write_bytes(b"synthetic package; native admission must verify")
        self.frontend = self.root / "native frontend"
        self.frontend.write_text("synthetic")
        self.frontend.chmod(0o700)
        self.profile = dict(id=uuid.uuid4().hex, name="iPod touch", base=str(self.base), state=str(self.root / "new state"), guest_package=str(self.package), bootrom="", audio=True)

    def test_kboot_has_no_hardware_rom_argument_or_shell(self):
        command = model.start_command(self.frontend, self.package, self.profile)
        self.assertIsInstance(command, list)
        self.assertNotIn("--rom", command)
        self.assertIn(str(self.base), command)
        self.assertFalse(Path(self.profile["state"]).exists())
        self.assertNotIn("--recover", command)

    def test_base_never_modified_and_native_still_verifies(self):
        before = (self.base / "device.lock.json").read_bytes()
        self.assertEqual(model.validate(self.profile)["strategy"], "kboot")
        self.assertEqual((self.base / "device.lock.json").read_bytes(), before)
        self.base.chmod(0o700)
        with self.assertRaisesRegex(model.SetupError, "writable"):
            model.validate(self.profile)

    def test_state_overlap_and_wrong_identity_block_before_start(self):
        for state in (self.base, self.base / "child", self.root):
            with self.subTest(state=state), self.assertRaisesRegex(model.SetupError, "separate"):
                model.validate(dict(self.profile, state=str(state)))
        state = Path(self.profile["state"])
        state.mkdir()
        (state / ".base-identity").write_text("a" * 64)
        with self.assertRaisesRegex(model.SetupError, "another base"):
            model.validate(self.profile)

    def test_dirty_recovery_is_explicit_and_never_deletes(self):
        state = Path(self.profile["state"])
        state.mkdir()
        dirty = state / ".dirty"
        dirty.write_text("retained")
        with self.assertRaisesRegex(model.SetupError, "Recover"):
            model.validate(self.profile)
        command = model.start_command(self.frontend, self.package, self.profile, allow_dirty=True)
        self.assertIn("--recover", command)
        self.assertEqual(dirty.read_text(), "retained")

    def test_profile_relative_remap_and_engine_injection_refused(self):
        profile = dict(self.profile, format=model.FORMAT, version=1, base="prepared base", guest_package="armv7.itpack", state="new state")
        config = self.root / "profile.json"
        config.write_text(json.dumps(profile))
        imported = model.load_profile(config)
        self.assertEqual(imported["base"], str(self.base))
        config.write_text(json.dumps(dict(profile, engine="/arbitrary/library.so")))
        with self.assertRaisesRegex(model.SetupError, "Unsupported profile"):
            model.load_profile(config)
        config.write_text('{"format":1,"format":2}')
        with self.assertRaisesRegex(model.SetupError, "Duplicate"):
            model.load_profile(config)

    def test_symlink_and_unbounded_config_refused(self):
        link = self.root / "alias"
        link.symlink_to(self.package)
        with self.assertRaises(model.SetupError):
            model.validate(dict(self.profile, guest_package=str(link)))
        oversized = self.root / "large.json"
        oversized.write_bytes(b" " * (model.MAX_CONFIG_BYTES + 1))
        with self.assertRaisesRegex(model.SetupError, "larger"):
            model.read_json(oversized)

    def test_import_cannot_overwrite_and_archive_requires_known_digest(self):
        with self.assertRaisesRegex(model.SetupError, "already exists"):
            model.import_command(self.frontend, self.package, self.base, "a" * 64)
        with self.assertRaisesRegex(model.SetupError, "SHA-256"):
            model.import_command(self.frontend, self.package, self.root / "destination", "")
        command = model.import_command(self.frontend, self.package, self.root / "destination", "A" * 64)
        self.assertEqual(command[-1], "a" * 64)

    def test_private_atomic_library_roundtrip_and_no_state_copy(self):
        store = model.Profiles(self.root / "library")
        store.save(self.profile)
        reloaded = model.Profiles(self.root / "library")
        self.assertEqual(reloaded.devices, [self.profile])
        self.assertEqual(reloaded.file.stat().st_mode & 0o777, 0o600)
        self.assertFalse(Path(self.profile["state"]).exists())
        self.assertFalse(list((self.root / "library").glob(".lighttouch-*")))


if __name__ == "__main__":
    unittest.main()
