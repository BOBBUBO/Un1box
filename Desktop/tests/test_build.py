"""Asset-free dispatch tests; execute only tiny synthetic Python recipes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("desktop_build", Path(__file__).resolve().parents[1] / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PlanTests(unittest.TestCase):
    def test_native_host_is_explicit_and_mac_ui_adoption_is_honest(self):
        plan = build.make_plan("macos-arm64", "bundle", "/private/mac output.app", ["--engine", "/private/libqemu-arm.dylib"], host=["linux", "x86_64"])
        self.assertFalse(plan["host_compatible"])
        self.assertFalse(plan["shared_guest_controls"])
        self.assertFalse(plan["guest_acceptance"])
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            with self.assertRaisesRegex(ValueError, "Native host required"):
                build.execute(plan, receipt)
            self.assertFalse(receipt.exists())

    def test_unimplemented_windows_route_never_builds_a_different_ui(self):
        with self.assertRaisesRegex(ValueError, "not integrated"):
            build.make_plan("windows-x86_64", "bundle", "/private/windows", [])

    def test_output_and_app_cannot_be_changed_by_trailing_recipe_flags(self):
        for flag in ["--output=/elsewhere", "--output", "--app=/other/source", "--app"]:
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "owned"):
                build.make_plan("linux-x86_64", "stage", "/private/stage", [flag])
        with self.assertRaisesRegex(ValueError, "outside"):
            build.make_plan("linux-x86_64", "stage", build.SOURCE / "artifacts", [])


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces"
        (self.source / "Desktop").mkdir(parents=True)
        target = {"schema": 1, "targets": {"fixture": {"hosts": [build.host_identity()], "ui": "synthetic", "shared_guest_controls": True, "qualification": "not a product", "actions": {"stage": {"recipe": "recipe.py", "fixed": [], "resource_control": "synthetic only"}}}}}
        (self.source / "Desktop/targets.json").write_text(json.dumps(target))
        (self.source / "recipe.py").write_text("import argparse,pathlib\np=argparse.ArgumentParser();p.add_argument('--output');p.add_argument('--payload');p.add_argument('--exit',type=int,default=0);a=p.parse_args();pathlib.Path(a.output).write_text(a.payload);raise SystemExit(a.exit)\n")
        for argv in [["init", "-q"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "synthetic dispatch fixture"]]:
            subprocess.run(["git", "-C", str(self.source)] + argv, check=True, capture_output=True)

    def test_actual_child_uses_literal_argv_and_receipt_binds_artifact(self):
        output = self.root / "artifact ; literal.txt"
        payload = "$(touch MUST_NOT_EXIST); exact literal"
        plan = build.make_plan("fixture", "stage", output, ["--payload", payload], self.source)
        receipt = self.root / "private/receipt.json"
        result = build.execute(plan, receipt)
        self.assertEqual(output.read_text(), payload)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["artifact_bytes"], len(payload))
        self.assertEqual(len(result["artifact_sha256"]), 64)
        self.assertFalse((self.source / "MUST_NOT_EXIST").exists())
        self.assertEqual(json.loads(receipt.read_text())["argv"], plan["argv"])
        with self.assertRaises(FileExistsError):
            build.execute(plan, receipt)

    def test_nonzero_child_is_recorded_as_failure(self):
        plan = build.make_plan("fixture", "stage", self.root / "failure", ["--payload", "failed", "--exit", "7"], self.source)
        result = build.execute(plan, self.root / "failed-receipt.json")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["exit_code"], 7)
        self.assertNotIn("artifact_sha256", result)

    def test_exit_zero_without_artifact_cannot_be_a_build_pass(self):
        output = self.root / "absent"
        plan = build.make_plan("fixture", "stage", output, ["--help"], self.source)
        receipt = self.root / "no-artifact-receipt.json"
        with self.assertRaisesRegex(ValueError, "declared output is absent"):
            build.execute(plan, receipt)
        self.assertEqual(json.loads(receipt.read_text())["status"], "FAIL")

    def test_receipt_cannot_be_the_artifact(self):
        output = self.root / "same-path"
        plan = build.make_plan("fixture", "stage", output, ["--payload", "no"], self.source)
        with self.assertRaisesRegex(ValueError, "separate"):
            build.execute(plan, output)
        self.assertFalse(output.exists())

    def test_dirty_source_rejected_before_child_and_failure_retained(self):
        (self.source / "untracked").write_text("dirty")
        output = self.root / "must not exist"
        plan = build.make_plan("fixture", "stage", output, ["--payload", "no"], self.source)
        receipt = self.root / "dirty-receipt.json"
        with self.assertRaisesRegex(ValueError, "Commit source"):
            build.execute(plan, receipt)
        self.assertFalse(output.exists())
        self.assertEqual(json.loads(receipt.read_text())["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
