"""Cross-host policy tests; actual Win32 operations require native Windows."""
import os
import unittest
from lighttouch_desktop.windows_storage import (checked_path, private_acl, WindowsStorage,
                                               SYSTEM_SID, ADMINISTRATORS_SID)

USER = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"


class PathPolicyTests(unittest.TestCase):
    def test_real_unicode_paths_and_separate_volume(self):
        self.assertEqual(checked_path("C:/Users/Blake/デバイス/guest.itpack"), "C:\\Users\\Blake\\デバイス\\guest.itpack")
        self.assertEqual(checked_path(r"D:\iOS bases\n72ap"), r"D:\iOS bases\n72ap")
        self.assertEqual(checked_path(r"\\server\share\assets\base"), r"\\server\share\assets\base")

    def test_reject_ambient_and_namespace_paths(self):
        for value in [r"C:relative", r"\ambient", "relative", r"\\?\C:\state", r"\\.\C:\state",
                      "//?/C:/state", "//./C:/state", "C:\\state\0oops"]:
            with self.subTest(value=value), self.assertRaises(ValueError): checked_path(value)

    def test_reject_streams_aliases_and_reserved_devices(self):
        for value in [r"C:\base\nor.bin:stream", r"C:\base\..\other", r"C:\base\.\nor.bin",
                      "C:\\base\\trailing.", "C:\\base\\trailing ", r"C:\base\con", r"C:\base\AUX.bin",
                      r"C:\base\lpt9.txt", "C:\\base\\bad\nname", "C:\\base\\?wild"]:
            with self.subTest(value=value), self.assertRaises(ValueError): checked_path(value)

    def test_non_reserved_prefix_is_ordinary_filename(self):
        self.assertEqual(checked_path(r"C:\state\console.bin"), r"C:\state\console.bin")


class PrivateAclTests(unittest.TestCase):
    def test_exact_user_system_administrators(self):
        self.assertTrue(private_acl(USER, 0x1000, [(0, USER), (0, SYSTEM_SID), (0, ADMINISTRATORS_SID)], USER))
        self.assertTrue(private_acl(USER, 0x1000, [(0, USER)], USER))

    def test_wrong_owner_inherited_or_null_acl(self):
        self.assertFalse(private_acl(OTHER, 0x1000, [(0, USER)], USER))
        self.assertFalse(private_acl(USER, 0, [(0, USER)], USER))
        self.assertFalse(private_acl(USER, 0x1000, [], USER))

    def test_external_users_and_everyone_rejected(self):
        for sid in (OTHER, "S-1-1-0", "S-1-5-11", "S-1-5-32-545"):
            with self.subTest(sid=sid):
                self.assertFalse(private_acl(USER, 0x1000, [(0, USER), (0, sid)], USER))

    def test_unknown_ace_not_guessed(self):
        for kind in (2, 5, 9, 255):
            with self.subTest(kind=kind):
                self.assertFalse(private_acl(USER, 0x1000, [(0, USER), (kind, USER)], USER))

    def test_denied_only_user_cannot_pass(self):
        self.assertFalse(private_acl(USER, 0x1000, [(1, USER), (0, SYSTEM_SID)], USER))

    @unittest.skipIf(os.name == "nt", "Checks that other hosts cannot silently substitute POSIX operations")
    def test_backend_requires_real_windows(self):
        with self.assertRaisesRegex(OSError, "requires Windows"):
            WindowsStorage()


if __name__ == "__main__": unittest.main()
