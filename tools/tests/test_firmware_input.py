import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "firmware-input.py"
spec = importlib.util.spec_from_file_location("firmware_input", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Response(io.BytesIO):
    headers = {}
    def geturl(self):
        return "https://source.example/file.ipsw"


class FirmwareInputTests(unittest.TestCase):
    def config(self, root, content=b"firmware"):
        return {"bytes": len(content), "sha1": hashlib.sha1(content).hexdigest(), "seconds": 5,
                "out": str(root / "input.ipsw"), "receipt": str(root / "receipt.json"),
                "urls": ["https://source.example/file.ipsw"]}

    def test_checked_content_only_becomes_final_after_full_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            result = module.acquire(config, opener=lambda *args, **kwargs: Response(b"firmware"))
            self.assertEqual("VERIFIED_INPUT_ONLY", result["status"])
            self.assertEqual(hashlib.sha256(b"firmware").hexdigest(), result["sha256"])
            self.assertEqual(b"firmware", (root / "input.ipsw").read_bytes())
            self.assertEqual(0o400, (root / "input.ipsw").stat().st_mode & 0o777)
            self.assertFalse((root / "input.ipsw.partial").exists())

    def test_failed_source_is_retained_before_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            config["urls"] += ["https://mirror.example/file.ipsw"]
            seen = []
            def opener(req, **kwargs):
                seen.append(req.full_url)
                return Response(b"corrupt!") if len(seen) == 1 else Response(b"firmware")
            result = module.acquire(config, opener=opener)
            self.assertEqual(["FAILED", "VERIFIED"], [a["status"] for a in result["attempts"]])
            self.assertEqual(b"corrupt!", (root / "input.ipsw.partial").read_bytes())

    def test_excess_bytes_and_bad_digest_never_become_final(self):
        for content in (b"extra firmware", b"corrupt!"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self.assertRaises(RuntimeError):
                    module.acquire(self.config(root), opener=lambda *args, **kwargs: Response(content))
                self.assertFalse((root / "input.ipsw").exists())
                self.assertEqual("FAILED", json.loads((root / "receipt.json").read_text())["status"])

    def test_refuses_existing_output_and_unreviewed_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.ipsw").write_bytes(b"existing")
            with self.assertRaises(ValueError):
                module.acquire(self.config(root))
            self.assertEqual(b"existing", (root / "input.ipsw").read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            config["seconds"] = 301
            with self.assertRaises(ValueError):
                module.acquire(config)

    def test_https_redirect_refused_before_insecure_request(self):
        with self.assertRaisesRegex(ValueError, "insecure"):
            module.HTTPSRedirects().redirect_request(None, None, 302, "Found", {}, "http://insecure.example/file")


if __name__ == "__main__":
    unittest.main()
