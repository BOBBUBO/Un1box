"""Offline protocol/integrity tests using upstream public JSON and synthetic bytes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services.catalog import CatalogClient, CatalogError, Target, _HTTPSRedirect, check_copy, incompatibility

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/store-compat"
N72 = Target("iPod2,1", "3.1.3", "armv6")
N81 = Target("iPod4,1", "4.2.1", "armv7")


class Response(io.BytesIO):
    status = 200
    def __init__(self, body, url):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.url = url
    def geturl(self):
        return self.url


class Opener:
    def __init__(self, *bodies):
        self.bodies, self.requests = list(bodies), []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return Response(self.bodies.pop(0), request.full_url)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.app = {"name": "Synthetic Test", "ipa_id": 123, "bundle_id": "org.example.synthetic",
                    "min_os": "3.0", "download_url": "https://legacystore.app/ipa/123"}
        self.payload = b"synthetic test bytes, not an application"
        self.details = {"ipa_id": "123", "bundle_id": self.app["bundle_id"], "available": True,
                        "size": len(self.payload), "md5": hashlib.md5(self.payload).hexdigest(),
                        "binary": {"install_status": "installable", "architectures": ["armv6"],
                                   "macho_min_os": "3.0", "device_family_macho": [1, 2]}}

    def test_search_always_names_device_and_os_and_identifies_client(self):
        opener = Opener({"apps": [self.app]})
        self.assertEqual(CatalogClient(opener=opener).search("test & more", N81), [self.app])
        request, timeout = opener.requests[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),
                         {"q": ["test & more"], "incompatible": ["include"], "limit": ["50"],
                          "device": ["iPod4,1"], "os": ["4.2.1"]})
        self.assertIn("LightTouchLinux", request.get_header("User-agent"))
        self.assertEqual(timeout, 30)

    def test_current_upstream_fixture_catches_relabelled_armv7(self):
        details = json.loads((FIXTURES / "new-copy-195588.json").read_text())
        app = json.loads((FIXTURES / "new-ipad1-3.2-enigmo-include.json").read_text())["apps"][0]
        with self.assertRaisesRegex(CatalogError, "newer processor"):
            check_copy(details, app, N72)
        check_copy(details, app, N81)
        old = json.loads((FIXTURES / "old-copy-195588.json").read_text())
        check_copy(old, app, N72)

    def test_numeric_family_and_os_from_real_fixture(self):
        details = json.loads((FIXTURES / "live-copy-1682.json").read_text())
        app = {"ipa_id": 1682, "bundle_id": details["bundle_id"], "min_os": "3.0"}
        check_copy(details, app, N81)
        for minimum in ("4.3", "3.x", "", "3..1", "-1", "0.1", "3.0.0.1"):
            with self.subTest(minimum=minimum), self.assertRaises(CatalogError):
                check_copy(details, dict(app, min_os=minimum), N81)

    def test_copy_encryption_architecture_family_and_identity_fail_before_transfer(self):
        changes = [{"available": False}, {"ipa_id": "124"}, {"bundle_id": "org.other"},
                   {"binary": None}, {"binary": {"install_status": "encrypted"}},
                   {"binary": {"install_status": "installable", "architectures": ["armv7"]}},
                   {"binary": dict(self.details["binary"], device_family_macho=[2])}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(CatalogError):
                check_copy(dict(self.details, **change), self.app, N72)

    def test_incompatible_rows_visible_but_not_downloadable(self):
        denied = dict(self.app, compat={"compatible": False, "reasons": ["encrypted"]})
        opener = Opener({"apps": [denied]}, {"apps": [denied]})
        client = CatalogClient(opener=opener)
        self.assertEqual(client.search("", N72, show_unavailable=False), [])
        with self.assertRaisesRegex(CatalogError, "Encrypted"):
            client.compatible_copy(123, N72)

    def test_revalidation_rejects_changed_identity_and_multiple_copies(self):
        for apps in ([dict(self.app, bundle_id="org.changed")], [self.app, self.app], []):
            opener = Opener({"apps": apps})
            with tempfile.TemporaryDirectory() as scratch, self.assertRaises(CatalogError):
                CatalogClient(opener=opener).download(self.app, N72, scratch)
            self.assertEqual(len(opener.requests), 1)

    def test_verified_download_is_private_and_reports_progress(self):
        opener = Opener({"apps": [self.app]}, self.details, self.payload)
        progress = []
        with tempfile.TemporaryDirectory() as scratch:
            path = CatalogClient(opener=opener).download(self.app, N72, scratch, progress=lambda *x: progress.append(x))
            self.assertEqual(path.read_bytes(), self.payload)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(progress[-1], (len(self.payload), len(self.payload)))
            self.assertEqual(list(path.parent.iterdir()), [path])
        self.assertEqual(len(opener.requests), 3)

    def test_bad_size_hash_and_partial_transfer_cleanup(self):
        for change, payload in [({"md5": "0" * 32}, self.payload), ({"md5": "bad"}, self.payload),
                                ({"size": len(self.payload) + 1}, self.payload), ({}, b""),
                                ({"size": 1}, self.payload), ({"size": True}, self.payload)]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as scratch:
                opener = Opener({"apps": [self.app]}, dict(self.details, **change), payload)
                with self.assertRaises(CatalogError):
                    CatalogClient(opener=opener).download(self.app, N72, scratch)
                self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_cancellation_cleans_partial_and_early_cancel_makes_no_request(self):
        opener = Opener({"apps": [self.app]}, self.details, self.payload)
        cancel = [False]
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaisesRegex(CatalogError, "cancelled"):
                CatalogClient(opener=opener).download(self.app, N72, scratch,
                    progress=lambda *args: cancel.__setitem__(0, True), cancelled=lambda: cancel[0])
            self.assertEqual(list(Path(scratch).iterdir()), [])
            empty = Opener()
            with self.assertRaisesRegex(CatalogError, "cancelled"):
                CatalogClient(opener=empty).download(self.app, N72, scratch, cancelled=lambda: True)
            self.assertEqual(empty.requests, [])

    def test_malformed_responses_and_insecure_redirect(self):
        for body in (b"not JSON", [], {"apps": [] , "ignored": 1}, {"apps": [None]},
                     {"apps": [dict(self.app, compat="invalid")]}):
            client = CatalogClient(opener=Opener(body))
            if body == {"apps": [], "ignored": 1}:
                self.assertEqual(client.search("", N72), [])
            else:
                with self.assertRaises(CatalogError):
                    client.search("", N72)
        with self.assertRaises(CatalogError):
            _HTTPSRedirect().redirect_request(Request("https://legacystore.app/ipa/123"), None, 302,
                                               "Found", {}, "http://example.invalid/app.ipa")

    def test_version_keys_are_url_encoded(self):
        opener = Opener({"data": []})
        self.assertEqual(CatalogClient(opener=opener).versions({"bundle_id": "org.example/a?b"}), [])
        self.assertIn("org.example%2Fa%3Fb/versions", opener.requests[0][0].full_url)


if __name__ == "__main__":
    unittest.main()
