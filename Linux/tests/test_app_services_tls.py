"""Host CA discovery and real loopback HTTPS; no catalog or IPA downloads."""
import contextlib
import http.server
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_services import catalog

HOST_CA_FILES = catalog._HOST_CA_FILES


class TLSContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="lighttouch-tls-test-")
        cls.root = Path(cls.temp.name)
        try:
            def openssl(*args):
                subprocess.run(["openssl", *map(str, args)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
            for name in ("trusted", "other"):
                openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=LightTouch synthetic {} CA".format(name),
                        "-addext", "basicConstraints=critical,CA:TRUE",
                        "-keyout", cls.root / (name + ".key"), "-out", cls.root / (name + ".pem"))
            openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
                    "-keyout", cls.root / "server.key", "-out", cls.root / "server.csr")
            (cls.root / "server.ext").write_text(
                "subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
                "extendedKeyUsage=serverAuth\n")
            openssl("x509", "-req", "-in", cls.root / "server.csr", "-CA", cls.root / "trusted.pem",
                    "-CAkey", cls.root / "trusted.key", "-CAcreateserial", "-days", "1",
                    "-extfile", cls.root / "server.ext", "-out", cls.root / "server.pem")
            cls.hashed = cls.root / "hashed"
            cls.hashed.mkdir()
            shutil.copyfile(cls.root / "trusted.pem", cls.hashed / "trusted.pem")
            openssl("rehash", cls.hashed)
        except BaseException:
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        environment = dict(os.environ)
        for key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            environment.pop(key, None)
        environment["NO_PROXY"] = "localhost,127.0.0.1"
        environment["no_proxy"] = "localhost,127.0.0.1"
        self.stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
        self.stack.enter_context(mock.patch.object(ssl.SSLContext, "load_default_certs", lambda *a: None))
        defaults = ssl.get_default_verify_paths()._replace(capath=None)
        self.stack.enter_context(mock.patch.object(ssl, "get_default_verify_paths", return_value=defaults))
        self.stack.enter_context(mock.patch.object(catalog, "_HOST_CA_FILES", ()))
        self.stack.enter_context(mock.patch.object(catalog, "_HOST_CA_DIRS", ()))

    def context(self):
        context = catalog._tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        return context

    @contextlib.contextmanager
    def server(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://127.0.0.1:{}/apps".format(self.server.server_port))
                    self.end_headers()
                else:
                    body = b'{"apps": []}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.root / "server.pem"), str(self.root / "server.key"))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())

    def test_default_ca_store_is_retained_without_loading_fallback(self):
        def defaults(context, *args):
            context.load_verify_locations(cafile=str(self.root / "trusted.pem"))
        with mock.patch.object(ssl.SSLContext, "load_default_certs", defaults), \
                mock.patch.object(catalog.os.path, "isfile", side_effect=AssertionError("unexpected fallback")):
            self.assertEqual(self.context().cert_store_stats()["x509_ca"], 1)

    def test_missing_default_and_host_stores_fail_actionably(self):
        with self.assertRaisesRegex(catalog.CatalogError, "ca-certificates.*SSL_CERT_FILE"):
            self.context()

    def test_debian_fedora_and_extracted_fedora_host_locations_are_discovered(self):
        original_load = ssl.SSLContext.load_verify_locations
        for available in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                          "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"):
            loaded = []
            def load(context, cafile=None, capath=None):
                loaded.append(cafile)
                return original_load(context, cafile=str(self.root / "trusted.pem"))
            with self.subTest(available=available), \
                    mock.patch.object(catalog, "_HOST_CA_FILES", HOST_CA_FILES), \
                    mock.patch.object(catalog.os.path, "isfile", side_effect=lambda path: path == available), \
                    mock.patch.object(ssl.SSLContext, "load_verify_locations", load):
                self.assertEqual(self.context().cert_store_stats()["x509_ca"], 1)
                self.assertEqual(loaded, [available])

    def test_missing_debian_default_finds_fedora_host_bundle(self):
        missing_debian = str(self.root / "debian-missing.pem")
        with mock.patch.object(catalog, "_HOST_CA_FILES", (missing_debian, str(self.root / "trusted.pem"))):
            self.assertEqual(self.context().cert_store_stats()["x509_ca"], 1)
            with self.server() as port:
                client = catalog.CatalogClient()
                client.BASE = "https://localhost:{}".format(port)
                self.assertEqual(client.search("", catalog.Target("iPod2,1", "3.1.3", "armv6")), [])

    def test_bad_host_file_can_fall_back_to_next_valid_host_bundle(self):
        invalid = self.root / "invalid.pem"
        invalid.write_text("this is not a certificate")
        with mock.patch.object(catalog, "_HOST_CA_FILES", (str(invalid), str(self.root / "trusted.pem"))):
            self.assertEqual(self.context().cert_store_stats()["x509_ca"], 1)
        with mock.patch.object(catalog, "_HOST_CA_FILES", (str(invalid),)):
            with self.assertRaisesRegex(catalog.CatalogError, "invalid.pem"):
                self.context()

    def test_host_hashed_directory_is_used_when_bundle_is_absent(self):
        with mock.patch.object(catalog, "_HOST_CA_DIRS", (str(self.hashed),)), self.server() as port:
            client = catalog.CatalogClient()
            with client._open("https://localhost:{}/apps".format(port)) as response:
                self.assertEqual(response.read(), b'{"apps": []}')

    def test_explicit_file_does_not_add_default_or_host_trust(self):
        os.environ["SSL_CERT_FILE"] = str(self.root / "other.pem")
        with mock.patch.object(catalog, "_HOST_CA_FILES", (str(self.root / "trusted.pem"),)), \
                mock.patch.object(ssl.SSLContext, "load_default_certs", side_effect=AssertionError("defaults")):
            self.assertEqual(self.context().cert_store_stats()["x509_ca"], 1)
            with self.server() as port, self.assertRaisesRegex(catalog.CatalogError, "CERTIFICATE_VERIFY_FAILED"):
                catalog.CatalogClient()._open("https://localhost:{}/apps".format(port))

    def test_explicit_trusted_file_connects(self):
        os.environ["SSL_CERT_FILE"] = str(self.root / "trusted.pem")
        self.context()
        with self.server() as port:
            with catalog.CatalogClient()._open("https://localhost:{}/apps".format(port)) as response:
                self.assertEqual(response.read(), b'{"apps": []}')

    def test_explicit_hashed_directory_connects_without_host_file(self):
        os.environ["SSL_CERT_DIR"] = str(self.hashed)
        self.context()
        with self.server() as port:
            with catalog.CatalogClient()._open("https://localhost:{}/apps".format(port)) as response:
                self.assertEqual(response.status, 200)

    def test_invalid_explicit_paths_and_empty_values_fail_without_fallback(self):
        choices = [{"SSL_CERT_FILE": str(self.root / "missing.pem")},
                   {"SSL_CERT_DIR": str(self.root / "missing-dir")},
                   {"SSL_CERT_FILE": ""}, {"SSL_CERT_DIR": ""}]
        for values in choices:
            with self.subTest(values=values), mock.patch.dict(os.environ, values), \
                    mock.patch.object(catalog, "_HOST_CA_FILES", (str(self.root / "trusted.pem"),)):
                with self.assertRaisesRegex(catalog.CatalogError, "configured TLS.*SSL_CERT_FILE / SSL_CERT_DIR"):
                    self.context()

    def test_hostname_verification_and_redirect_verification_remain_enabled(self):
        os.environ["SSL_CERT_FILE"] = str(self.root / "trusted.pem")
        with self.server() as port:
            for url in ("https://127.0.0.1:{}/apps".format(port), "https://localhost:{}/redirect".format(port)):
                with self.subTest(url=url), self.assertRaisesRegex(catalog.CatalogError, "CERTIFICATE_VERIFY_FAILED"):
                    catalog.CatalogClient()._open(url)


if __name__ == "__main__":
    unittest.main()
