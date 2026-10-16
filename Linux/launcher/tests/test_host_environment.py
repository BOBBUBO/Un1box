"""Ordinary guest launches remain cache-off with or without guest services."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host import LinuxHost


class HostEnvironmentTests(unittest.TestCase):
    def test_missing_services_scrub_inherited_cache_before_guest_spawn(self):
        args = SimpleNamespace(usbmuxd="/absent/usbmuxd", imobile_library="/absent/mobile", plist_library="/absent/plist")
        inherited = {"LT_TCG_IR_CACHE_ENABLE": "experimental-v1", "LT_TCG_IR_CACHE": "/untrusted", "DISPLAY": ":99"}
        with patch.dict(os.environ, inherited, clear=True):
            bridge, environment = LinuxHost.start_services(args, Path("/private/library"), "a" * 32)
            self.assertIsNone(bridge)
            self.assertEqual(environment, {"DISPLAY": ":99"})
            self.assertEqual(dict(os.environ), inherited)

    def test_services_keep_endpoints_while_cache_opt_in_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auxiliary = root / "auxiliary"
            auxiliary.touch()
            args = SimpleNamespace(usbmuxd=str(auxiliary), imobile_library=str(auxiliary), plist_library=str(auxiliary))
            source = {"LT_TCG_IR_CACHE_ENABLE": "experimental-v1", "LT_TCG_IR_CACHE": "/untrusted", "USBMUXD_SOCKET_ADDRESS": "UNIX:/private/socket"}
            with patch("host.Bridge") as factory:
                factory.return_value.start.return_value = source
                bridge, environment = LinuxHost.start_services(args, root, "a" * 32)
                self.assertIs(bridge, factory.return_value)
                self.assertEqual(environment, {"USBMUXD_SOCKET_ADDRESS": "UNIX:/private/socket"})
                self.assertIn("LT_TCG_IR_CACHE", source)
                factory.return_value.close.assert_not_called()
