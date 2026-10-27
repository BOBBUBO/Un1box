"""Private app-folder migration guards; daemon/native libraries never run."""
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.bridge import Bridge
from app_services.ipa import InstallError


class BridgeDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o755)
        self.tool = self.root / "not-executed"
        self.tool.write_bytes(b"synthetic tool/library")
        self.pairing = self.root / "pairing"
        self.logs = self.root / "service-logs"
        self.bridge = Bridge(self.tool, self.tool, self.tool, self.pairing, self.logs)
        self.addCleanup(self.bridge.close)

    def folders(self):
        for path in (self.pairing, self.logs):
            path.mkdir(mode=0o755)
            path.chmod(0o755)

    def reject_without_daemon(self, expected_path, reason):
        with patch("app_services.bridge.subprocess.Popen") as popen:
            with self.assertRaises(InstallError) as raised:
                self.bridge.start()
            popen.assert_not_called()
        self.assertIn(str(expected_path), str(raised.exception))
        self.assertIn(reason, str(raised.exception))

    def test_owned_755_migration_is_fd_checked_and_only_exact_folders_change(self):
        self.folders()
        sibling = self.root / "unrelated"
        sibling.mkdir(mode=0o755)
        sibling.chmod(0o755)
        saved = self.pairing / "existing-record"
        saved.write_bytes(b"retained pairing data")
        saved.chmod(0o644)
        identities = {(p.stat().st_dev, p.stat().st_ino) for p in (self.pairing, self.logs)}
        changed = []
        real_fchmod = os.fchmod
        def chmod(fd, mode):
            info = os.fstat(fd)
            self.assertEqual(info.st_uid, os.geteuid())
            self.assertIn((info.st_dev, info.st_ino), identities)
            changed.append((info.st_dev, info.st_ino))
            return real_fchmod(fd, mode)
        def launch(*args, **kwargs):
            self.assertEqual(stat.S_IMODE(self.pairing.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.logs.stat().st_mode), 0o700)
            raise RuntimeError("synthetic launch intercepted")
        with patch("app_services.bridge.os.fchmod", side_effect=chmod), \
                patch("app_services.bridge.subprocess.Popen", side_effect=launch) as popen:
            with self.assertRaisesRegex(RuntimeError, "intercepted"):
                self.bridge.start()
            popen.assert_called_once()
        self.assertEqual(set(changed), identities)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(sibling.stat().st_mode), 0o755)
        self.assertEqual(saved.read_bytes(), b"retained pairing data")
        self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o644)

    def test_new_nested_folders_are_private_without_chmodding_existing_parent(self):
        target = self.root / "devices" / "new-device" / "pairing"
        Bridge._prepare_data_directory(target)
        for path in (target, target.parent, target.parent.parent):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_foreign_fd_owner_names_exact_path_and_never_chmods_or_starts(self):
        self.folders()
        self.pairing.chmod(0o700)
        identity = (self.logs.stat().st_dev, self.logs.stat().st_ino)
        real_fstat = os.fstat
        def foreign(fd):
            info = real_fstat(fd)
            if (info.st_dev, info.st_ino) == identity:
                values = list(info)
                values[4] = os.geteuid() + 1  # Synthetic foreign ownership; no chown/sudo.
                return os.stat_result(values)
            return info
        with patch("app_services.bridge.os.fstat", side_effect=foreign), \
                patch("app_services.bridge.os.fchmod") as chmod:
            self.reject_without_daemon(self.logs, "owned by uid")
            chmod.assert_not_called()
        self.assertEqual(stat.S_IMODE(self.logs.stat().st_mode), 0o755)

    def test_final_symlink_names_path_and_leaves_target_untouched(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        outside.chmod(0o755)
        self.logs.symlink_to(outside, target_is_directory=True)
        self.reject_without_daemon(self.logs, "symbolic link")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)

    def test_symlink_ancestor_is_not_followed_or_modified(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        alias = self.root / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        self.bridge.pairing = alias / "pairing"
        self.reject_without_daemon(self.bridge.pairing, "symbolic link")
        self.assertFalse((outside / "pairing").exists())
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)

    def test_regular_file_names_exact_path_before_daemon_start(self):
        self.logs.write_bytes(b"keep this unrelated file")
        self.reject_without_daemon(self.logs, "not a directory")
        self.assertEqual(self.logs.read_bytes(), b"keep this unrelated file")

    def test_fd_chmod_failure_names_path_and_reason_before_daemon_start(self):
        self.folders()
        with patch("app_services.bridge.os.fchmod", side_effect=PermissionError(errno.EPERM, "Operation not permitted")):
            self.reject_without_daemon(self.pairing, "Operation not permitted")

    def test_replacement_with_symlink_during_migration_cannot_chmod_target(self):
        self.folders()
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        outside.chmod(0o755)
        held = self.root / "original-pairing"
        real_fchmod = os.fchmod
        def replace(fd, mode):
            self.pairing.rename(held)
            self.pairing.symlink_to(outside, target_is_directory=True)
            real_fchmod(fd, mode)
        with patch("app_services.bridge.os.fchmod", side_effect=replace):
            self.reject_without_daemon(self.pairing, "changed during setup")
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(held.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
