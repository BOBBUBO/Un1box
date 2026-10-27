import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "firmware-matrix.py"
spec = importlib.util.spec_from_file_location("firmware_matrix", SCRIPT)
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)
ROOT = SCRIPT.parents[1]


class FirmwareMatrixTests(unittest.TestCase):
    def test_real_catalog_preserves_upstream_uncertainty_and_sibling_dependencies(self):
        result = matrix.enumerate_catalog(ROOT / "Portable/firmware/firmware-catalog.json", all_catalog=True)
        self.assertEqual(329, result["entry_count"])
        self.assertEqual(8, len({r["board"] for r in result["entries"]}))
        self.assertEqual(22, sum(bool(r["sibling_entry"]) for r in result["entries"]))
        self.assertEqual(103, sum(r["source"]["kind"] == "rar" for r in result["entries"]))
        self.assertTrue(all(r["prepared"] == "not_assessed" for r in result["entries"]))
        self.assertTrue(all(set(r["target_acceptance"].values()) == {"not_assessed"} for r in result["entries"]))
        self.assertNotIn('"keys":', json.dumps(result))
        rows = {r["id"]: r for r in result["entries"]}
        self.assertEqual("bootrom_240_4", rows["n72ap-7E18"]["boot"]["external_rom"])
        self.assertEqual("kboot", rows["n81ap-8C148"]["boot"]["strategy"])
        self.assertIsNone(rows["n81ap-8C148"]["boot"]["external_rom"])
        self.assertEqual("beta", rows["k48ap-8C5091e"]["prerelease"])
        self.assertEqual(1, rows["k48ap-8C5091e"]["prerelease_number"])
        self.assertEqual({"reference": None, "sha256": None, "lock_sha256": None, "status": "not_assessed"},
                         rows["n81ap-8C148"]["immutable_base"])
        self.assertIn("nor.bin", rows["n81ap-8C148"]["boot"]["files_from_preparation"])
        later_1g = [r for r in result["entries"] if r["board"] in ("m68ap", "n45ap") and not r["version"].startswith("1.")]
        self.assertTrue(later_1g)
        self.assertTrue(all(r["notes"] for r in later_1g))

    def test_stable_only_selection_is_exactly_six_available_builds(self):
        result = matrix.enumerate_catalog(ROOT / "Portable/firmware/firmware-catalog.json", stable_only=True)
        self.assertEqual("upstream_available_only", result["selection"])
        self.assertEqual(329, result["catalog_entry_count"])
        self.assertEqual(6, result["entry_count"])
        self.assertEqual({"n72ap-5G77a", "n72ap-5H11a", "n72ap-7E18", "k48ap-7B367", "k48ap-7B500", "k48ap-8C148"},
                         {e["id"] for e in result["entries"]})
        self.assertEqual({"available"}, {e["upstream_status"] for e in result["entries"]})

    def test_default_selection_has_stable_first_then_explicit_release_majors(self):
        result = matrix.enumerate_catalog(ROOT / "Portable/firmware/firmware-catalog.json")
        self.assertEqual("stable_first_and_representative_releases", result["selection"])
        self.assertEqual(11, result["entry_count"])
        self.assertEqual({"available"}, {e["upstream_status"] for e in result["entries"][:6]})
        self.assertEqual({"experimental"}, {e["upstream_status"] for e in result["entries"][6:]})
        self.assertEqual({1, 2, 3, 4, 5, 6, 7}, {e["representative_major"] for e in result["entries"] if e["representative_major"]})
        self.assertFalse(any(e["prerelease"] for e in result["entries"]))
        self.assertEqual(set(matrix.REPRESENTATIVE_RELEASES),
                         {e["id"] for e in result["entries"] if e["representative_major"]})

    def test_build_wildcard_matches_whole_leading_number(self):
        self.assertTrue(matrix.build_matches(["8*"], "8C148"))
        self.assertFalse(matrix.build_matches(["1*"], "10B500"))
        self.assertTrue(matrix.build_matches(["7A341"], "7A341"))
        self.assertFalse(matrix.build_matches(["7A341"], "7A342"))

    def blob(self, root, entries, stream):
        data = json.dumps({"entries": entries}).encode()
        path = root / "input.itpack"
        path.write_bytes(b"ITPACK01" + struct.pack("<I", len(data)) + data + zlib.compress(stream))
        return path

    def test_manifest_selection_respects_board_arch_build_and_stub(self):
        manifest = {"family": "test-family", "arch": "armv7", "serial": 1, "version": "test", "stub": True,
                    "requires": {"boards": ["n81ap"], "builds": ["8*"]}}
        raw = json.dumps(manifest).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.blob(Path(tmp), [{"name": "test-family/manifest.json", "size": len(raw)}], raw)
            pack = matrix.read_guest_manifests(path)
            result = matrix.enumerate_catalog(ROOT / "Portable/firmware/firmware-catalog.json", [pack], all_catalog=True)
        rows = {r["id"]: r for r in result["entries"]}
        self.assertEqual("test-family", rows["n81ap-8C148"]["matching_guest_packages"][0]["family"])
        self.assertTrue(rows["n81ap-8C148"]["matching_guest_packages"][0]["stub"])
        self.assertEqual([], rows["n72ap-7E18"]["matching_guest_packages"])
        self.assertEqual([], rows["n90ap-8C148"]["matching_guest_packages"])

    def test_rejects_path_traversal_duplicate_negative_and_truncated_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for entries in ([{"name": "../escape", "size": 0}],
                            [{"name": "same", "size": 0}] * 2,
                            [{"name": "a", "size": -1}],
                            [{"name": "a", "size": True}]):
                with self.assertRaises(ValueError):
                    matrix.read_index(self.blob(root, entries, b""))
            path = self.blob(root, [{"name": "family/manifest.json", "size": 9}], b"{}")
            with self.assertRaises(ValueError):
                matrix.read_guest_manifests(path)
            path = self.blob(root, [{"name": "family/manifest.json", "size": 2}], b"{}")
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError):
                matrix.read_guest_manifests(path)

    def test_rejects_zip_bomb_before_decompression(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.blob(Path(tmp), [{"name": "family/manifest.json", "size": matrix.MAX_GUEST_STREAM + 1}], b"")
            with self.assertRaisesRegex(ValueError, "256 MiB"):
                matrix.read_guest_manifests(path)
            path = self.blob(Path(tmp), [{"name": "family/manifest.json", "size": 1}], b"x" * 100000)
            with self.assertRaisesRegex(ValueError, "indexed size"):
                matrix.read_guest_manifests(path)

    def test_refuses_broken_source_contract_and_missing_sibling(self):
        source = json.loads((ROOT / "Portable/firmware/firmware-catalog.json").read_text())
        direct = next(e for e in source["entries"] if e["id"] == "n72ap-7E18")
        wrapped = next(e for e in source["entries"] if e["source"]["kind"] == "rar")
        sibling = next(e for e in source["entries"] if e["recipe"].get("keybag_ramdisk_from"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            broken = json.loads(json.dumps(wrapped))
            del broken["source"]["archive_sha1"]
            for entries, why in (([direct, direct], "duplicate"), ([broken], "wrapped-source"),
                                 ([sibling], "missing sibling")):
                path.write_text(json.dumps({"format": 1, "entries": entries}))
                with self.assertRaisesRegex(ValueError, why):
                    matrix.enumerate_catalog(path, all_catalog=True)

    def test_prepared_lock_projection_never_marks_contents_or_runtime_verified(self):
        import hashlib
        result = matrix.enumerate_catalog(ROOT / "Portable/firmware/firmware-catalog.json", all_catalog=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "device.lock.json"
            lock = {"format": 1, "board": "n81ap", "build": "8C148", "boot_strategy": "kboot",
                    "guest_package": {"family": "k48-ios4", "seed": 26, "version": "1.1.24",
                                      "itpack": {"sha256": "a" * 64}}}
            path.write_text(json.dumps(lock))
            projected = matrix.attach_lock_metadata(result, [path])
            row = next(r for r in projected["entries"] if r["id"] == "n81ap-8C148")
            self.assertEqual("lock_metadata_only", row["immutable_base"]["status"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["immutable_base"]["lock_sha256"])
            self.assertEqual("k48-ios4", row["prepared_guest_package"]["family"])
            self.assertEqual("not_assessed", row["prepared"])
            self.assertEqual({"not_assessed"}, set(row["target_acceptance"].values()))
            lock["boot_strategy"] = "bootrom"
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "boot strategy"):
                matrix.attach_lock_metadata(result, [path])

    def test_plan_never_executes_and_refuses_nonselected_build(self):
        result = matrix.preparation_plan(ROOT / "Portable/firmware/firmware-catalog.json",
                                         "n72ap-5G77a", "/private/input.ipsw", "/private/new-run", "/tool", "/helper", "/guest")
        self.assertFalse(result["execution_authorized"])
        self.assertEqual([], result["blockers"])
        self.assertIn("--helper", result["create_argv"])
        self.assertFalse(Path("/private/new-run").exists())
        with self.assertRaisesRegex(ValueError, "entry not found"):
            matrix.preparation_plan(ROOT / "Portable/firmware/firmware-catalog.json",
                                    "n81ap-8G4", "/private/input.ipsw", "/private/new-run", "/tool", None, None)


if __name__ == "__main__":
    unittest.main()
