"""Native binding contracts with synthetic pointers; no devices or DLL execution."""
import ctypes as C
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services import mobile
from app_services.ipa import InstallError


class MobileLoadingTests(unittest.TestCase):
    def load(self, platform="win32", *, old_plist=False, separate_dirs=False):
        plist_names = ["plist_from_xml", "plist_to_xml", "plist_free"]
        if not old_plist:
            plist_names.append("plist_mem_free")
        plist = mock.Mock(spec=plist_names)
        lib, libc = mock.Mock(), mock.Mock()
        lib.idevice_new.return_value = 0
        lib.lockdownd_client_new_with_handshake.return_value = 0
        library = Path("bundle/mobile.dll").resolve()
        plist_library = Path("other/plist.dll" if separate_dirs else "bundle/plist.dll").resolve()
        handles = [mock.Mock(), mock.Mock()]
        with mock.patch.object(mobile.sys, "platform", platform), \
                mock.patch.object(mobile.os, "add_dll_directory", create=True,
                                  side_effect=handles) as add_directory, \
                mock.patch.object(mobile.C, "CDLL", side_effect=[plist, lib, libc]) as cdll:
            value = mobile.Mobile(library, plist_library)
        return value, cdll, add_directory, handles, library, plist_library

    def test_windows_uses_absolute_cdecl_libraries_and_retains_search_handle(self):
        value, cdll, add_directory, handles, library, plist = self.load()
        self.assertEqual(cdll.call_args_list,
                         [mock.call(str(plist)), mock.call(str(library)), mock.call("msvcrt.dll")])
        add_directory.assert_called_once_with(str(library.parent))
        self.assertEqual(value._dll_directories, [handles[0]])
        handles[0].close.assert_not_called()
        self.assertEqual(value.libc.free.argtypes, [mobile.P])
        self.assertIsNone(value.libc.free.restype)
        self.assertEqual(value.plist.plist_mem_free.argtypes, [mobile.P])
        self.assertIsNone(value.plist.plist_mem_free.restype)

    def test_service_start_retries_with_a_fresh_handshake_after_a_mux_error(self):
        value, *_ = self.load("linux")
        lib = value.lib
        starts = iter([-8, 0])  # lockdownd dropped the idle client, then the new one works

        def start(lockdown, name, descriptor):
            code = next(starts)
            if code == 0:
                C.cast(descriptor, C.POINTER(mobile.P))[0] = 0x1234
            return code
        lib.lockdownd_start_service.side_effect = start
        lib.instproxy_client_new.return_value = 0
        with mock.patch.object(mobile.time, "sleep"):
            value.service("com.apple.mobile.installation_proxy", "instproxy")
        self.assertEqual(lib.lockdownd_start_service.call_count, 2)
        self.assertEqual(lib.lockdownd_client_new_with_handshake.call_count, 2)  # Mobile() and the retry

    def test_service_start_reports_the_error_after_three_attempts(self):
        value, *_ = self.load("linux")
        value.lib.lockdownd_start_service.return_value = -8
        with mock.patch.object(mobile.time, "sleep"):
            with self.assertRaisesRegex(InstallError, r"start com.apple.afc failed \(device error -8\)"):
                value.service("com.apple.afc", "afc")
        self.assertEqual(value.lib.lockdownd_start_service.call_count, 3)

    def test_windows_registers_both_provider_directories(self):
        value, _, add_directory, handles, library, plist = self.load(separate_dirs=True)
        self.assertEqual(add_directory.call_args_list,
                         [mock.call(str(library.parent)), mock.call(str(plist.parent))])
        self.assertEqual(value._dll_directories, handles)

    def test_posix_preserves_global_plist_loading_and_process_allocator(self):
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                value, cdll, add_directory, _, library, plist = self.load(platform)
                self.assertEqual(cdll.call_args_list,
                                 [mock.call(str(plist), mode=C.RTLD_GLOBAL),
                                  mock.call(str(library)), mock.call(None)])
                add_directory.assert_not_called()
                self.assertEqual(value._dll_directories, [])

    def test_older_posix_plist_can_use_its_shared_libc_allocator(self):
        value, *_ = self.load("linux", old_plist=True)
        self.assertIs(value._plist_mem_free, value.libc.free)

    def xml_output(self, value, payload, length=None):
        buffer = C.create_string_buffer(payload)

        def output(node, pointer, size):
            C.cast(pointer, C.POINTER(mobile.P))[0] = C.cast(buffer, mobile.P)
            C.cast(size, C.POINTER(C.c_uint32))[0] = len(payload) if length is None else length

        value.plist.plist_to_xml.side_effect = output
        return C.cast(buffer, mobile.P).value

    def test_decode_releases_xml_through_plist_allocator_on_success_or_parse_error(self):
        for payload in (plistlib.dumps({"ProductType": "iPod2,1"}), b"invalid XML"):
            with self.subTest(payload=payload[:20]):
                value, *_ = self.load()
                pointer = self.xml_output(value, payload)
                if payload == b"invalid XML":
                    with self.assertRaises(plistlib.InvalidFileException):
                        value.decode(mobile.P(1))
                else:
                    self.assertEqual(value.decode(mobile.P(1)), {"ProductType": "iPod2,1"})
                self.assertEqual(value.plist.plist_mem_free.call_args.args[0].value, pointer)
                self.assertEqual(value.plist.plist_mem_free.call_count, 1)
                value.libc.free.assert_not_called()

    def test_decode_releases_oversized_xml_and_rejects_null_without_freeing(self):
        value, *_ = self.load()
        pointer = self.xml_output(value, b"x", 16 * 1024**2 + 1)
        with self.assertRaises(InstallError):
            value.decode(mobile.P(1))
        self.assertEqual(value.plist.plist_mem_free.call_args.args[0].value, pointer)
        value.plist.plist_mem_free.reset_mock()
        value.plist.plist_to_xml.side_effect = None
        with self.assertRaises(InstallError):
            value.decode(mobile.P(1))
        value.plist.plist_mem_free.assert_not_called()
        value.libc.free.assert_not_called()

    def test_afc_string_uses_msvcrt_allocator_even_when_install_cannot_proceed(self):
        value, *_ = self.load()
        buffer = C.create_string_buffer(b"1")

        def device_info(client, key, pointer):
            C.cast(pointer, C.POINTER(mobile.P))[0] = C.cast(buffer, mobile.P)
            return 0

        value.lib.afc_get_device_info_key.side_effect = device_info
        value.service = mock.Mock(return_value=mobile.P(7))
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / "synthetic.ipa"
            ipa.write_bytes(b"x")
            with self.assertRaisesRegex(InstallError, "needs .* free"):
                value.install(ipa, "org.example.synthetic", mock.Mock())
        value.libc.free.assert_called_once()
        self.assertEqual(value.libc.free.call_args.args[0].value, C.cast(buffer, mobile.P).value)
        value.plist.plist_mem_free.assert_not_called()
        value.lib.afc_client_free.assert_called_once()


if __name__ == "__main__":
    unittest.main()
