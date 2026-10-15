import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("stage", Path(__file__).resolve().parents[1] / "stage-appimage.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class PrivacyTests(unittest.TestCase):
    def test_host_wayland_cannot_be_shadowed_by_bundled_libraries(self):
        for soname in ("libwayland-client.so.0", "libwayland-server.so.0",
                       "libwayland-cursor.so.0", "libwayland-egl.so.1"):
            with self.subTest(soname=soname):
                self.assertIn(soname, stage.HOST_LIBRARIES)
                with self.assertRaisesRegex(ValueError, "graphics provider"):
                    stage.target("usr/lib/" + soname)

    def test_loader_abi_tags_cannot_bypass_glibc_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-elf"
            header = bytearray(20)
            header[:6] = b"\x7fELF\x02\x01"
            header[18:20] = b"\x3e\x00"
            path.write_bytes(header)
            for required in ("GLIBC_2.38", "GLIBC_ABI_DT_RELR", "GLIBC_PRIVATE"):
                with self.subTest(required=required), mock.patch.object(stage.subprocess, "run") as run:
                    run.return_value.stdout = required
                    with self.assertRaises(ValueError):
                        stage.elf_requirements(path)
                    self.assertEqual(run.call_count, 1)
            with mock.patch.object(stage.subprocess, "run") as run:
                run.side_effect = [mock.Mock(stdout="GLIBC_2.2.5 GLIBC_2.31"), mock.Mock(stdout="")]
                self.assertEqual(stage.elf_requirements(path), [])

    def test_private_assets_and_escape_paths_rejected(self):
        for value in ("../guest.itpack", "/usr/lib/file", "usr/../secret", "usr/nand/chunk", "usr/lib/nor.bin", "usr/assets/firmware.ipsw", "usr/lib/guest.itpack", "usr/lib/libc.so.6", "usr/lib/libEGL.so.1", "usr/lib/libstdc++.so.6", "usr/lib/libgcc_s.so.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                stage.target(value)
        self.assertEqual(str(stage.target("usr/lib/lighttouch/libqemu-arm.so")), "usr/lib/lighttouch/libqemu-arm.so")

    def test_failed_manifest_cannot_create_package_or_copy_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_text("synthetic")
            manifest = dict(schema=1, architecture="x86_64", glibc_max="2.31", python_stdlib="python3.9", files=[dict(source=str(source), destination="usr/bin/lighttouch-linux", sha256=hashlib.sha256(source.read_bytes()).hexdigest(), license="test-only")])
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "must include"):
                stage.stage(path, root / "AppDir")
            self.assertFalse((root / "AppDir").exists())
            manifest["files"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                stage.read_manifest(path)


if __name__ == "__main__":
    unittest.main()
