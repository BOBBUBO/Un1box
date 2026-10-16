import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "firmware-prepare.py"
spec = importlib.util.spec_from_file_location("firmware_prepare", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakePreparation(module.Supervisor):
    """Run real process supervision on Linux without any firmware or macOS actions."""
    def preflight(self):
        self.run.mkdir(mode=0o700)
        self.owns_run = True
        table = module.process_table(self.work_end)
        self.result["resident_avd_before"] = table[os.getpid()]

    def argv(self):
        return [sys.executable, "-c", self.config["script"]]

    def verify(self):
        self.result["fake_verifier_called"] = True


class FirmwarePrepareTests(unittest.TestCase):
    def config(self, root, script="print('done')"):
        return dict(root=str(root), resident_avd_pid=os.getpid(), script=script, service_state=str(root / "service"))

    def limits(self, **kw):
        return dict(module.LIMITS, total=4, work=2, term_end=3, free_disk=0, **kw)

    def test_real_subprocess_success_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "mounted_images", return_value={"images": []}):
            root = Path(tmp)
            first = FakePreparation(self.config(root), self.limits()).execute()
            self.assertEqual("CONTENT_VERIFIED_REVIEW_REQUIRED", first["status"])
            self.assertTrue(first["fake_verifier_called"])
            receipt = (root / "create-supervised-01/RECEIPT.json").read_bytes()
            second = FakePreparation(self.config(root), self.limits()).execute()
            self.assertEqual("FAILED", second["status"])
            self.assertEqual(receipt, (root / "create-supervised-01/RECEIPT.json").read_bytes())

    def test_actual_pinned_help_matches_all_emitted_flags_and_refuses_old_language_flag(self):
        help_text = (Path(__file__).parent / "fixtures/firmwarekit-0.2.0-create-help.txt").read_text()
        config = dict(self.config(Path("/private/pilot")), tool="/pinned/firmwarekit", entry="/input/entry.json",
                      ipsw="/input/firmware.ipsw", staging="/private/pilot/prepared", seed="test",
                      guest_tools="/input/guest-tools", cache="/private/pilot/cache")
        argv = module.Supervisor(config).argv()
        module.validate_create_argv(argv, help_text)
        self.assertNotIn("--language", argv)
        with self.assertRaisesRegex(ValueError, "argument contract"):
            module.validate_create_argv(argv + ["--language", "en-US"], help_text)
        with self.assertRaisesRegex(ValueError, "does not offer"):
            module.validate_create_argv(argv, help_text.replace("--cache", "--old-cache"))

    def test_successor_run_preserves_original_phase_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "mounted_images", return_value={"images": []}):
            root = Path(tmp)
            old = root / "create-supervised-01"
            old.mkdir()
            (old / "RECEIPT.json").write_text('{"status":"FAILED"}')
            config = dict(self.config(root), run_name="create-supervised-02")
            result = FakePreparation(config, self.limits()).execute()
            self.assertEqual("CONTENT_VERIFIED_REVIEW_REQUIRED", result["status"])
            self.assertEqual('{"status":"FAILED"}', (old / "RECEIPT.json").read_text())
            with self.assertRaisesRegex(ValueError, "run name"):
                module.Supervisor(dict(config, run_name="../escape"))

    def test_actual_pinned_help_refused_before_writable_preparation_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            help_text = (Path(__file__).parent / "fixtures/firmwarekit-0.2.0-create-help.txt").read_bytes()
            tool = root / "tool"
            entry = root / "entry.json"
            ipsw = root / "input.ipsw"
            for p in (tool, entry, ipsw):
                p.write_bytes(b"synthetic input")
            config = dict(self.config(root), execution_authorized=True, phase="create-only",
                 tool=str(tool), entry=str(entry), ipsw=str(ipsw), seed="test",
                 staging=str(root / "prepared"), cache=str(root / "cache"),
                 service_state=str(root / "service"), guest_tools=str(root / "guest"),
                 create_help_sha256="0"*64)
            for name in ("tool", "entry", "ipsw"):
                config[name + "_sha256"] = hashlib.sha256(Path(config[name]).read_bytes()).hexdigest()
            instance = module.Supervisor(config)
            with patch.object(module.sys, "platform", "darwin"), patch.object(module, "helper", return_value=help_text):
                with self.assertRaisesRegex(ValueError, "help digest changed"):
                    instance.preflight()
            self.assertFalse(any(Path(config[name]).exists() for name in ("staging", "cache", "service_state")))

    def test_exact_violating_rss_and_process_count_are_durable_before_raise(self):
        for count, rss, message in ((1, module.LIMITS["rss"]+123, "RSS"), (33, 100, "process count")):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                instance = module.Supervisor(self.config(Path(tmp)))
                instance.run.mkdir()
                resident = dict(identity="resident-start")
                instance.result["resident_avd_before"] = resident
                processes = [dict(pid=i+100, identity="exact-start", rss=rss, command="firmwarekit") for i in range(count)]
                with patch.object(instance, "observe", return_value=(processes, {os.getpid(): resident})):
                    with self.assertRaisesRegex(RuntimeError, message):
                        instance.sample()
                receipt = json.loads((instance.run / "RECEIPT.json").read_text())
                sample = receipt["samples"][-1]
                self.assertEqual(processes, sample["processes"])
                self.assertEqual(count * rss, sample["rss"])
                self.assertEqual(count, sample["owned_process_count"])
                self.assertIn(message, sample["violations"][0])
                self.assertNotIn("cleanup", receipt)

    def test_create03_explicit_profile_and_host_memory_floor_preserve_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = module.Supervisor(dict(self.config(Path(tmp)), resource_profile="n72-create03-4gib"))
            self.assertEqual(4*module.GIB, instance.limits["rss"])
            self.assertEqual(8*module.GIB, instance.limits["free_memory"])
            instance.run.mkdir()
            resident = dict(identity="resident")
            instance.result["resident_avd_before"] = resident
            processes = [dict(pid=100, identity="start", rss=3*module.GIB, command="firmwarekit")]
            memory = b"Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 10.\nPages inactive: 20.\n"
            def observer(argv, *args, **kwargs):
                return memory if argv[0] == "vm_stat" else b"0 /private/pilot\n"
            with patch.object(instance, "observe", return_value=(processes, {os.getpid(): resident})), patch.object(module, "helper", side_effect=observer):
                with self.assertRaisesRegex(RuntimeError, "memory reserve"):
                    instance.sample()
            sample = json.loads((instance.run / "RECEIPT.json").read_text())["samples"][-1]
            self.assertEqual(30*16384, sample["host_free_plus_inactive"])
            self.assertEqual(memory.decode(), sample["vm_stat"])
            with self.assertRaisesRegex(ValueError, "unreviewed resource"):
                module.Supervisor(dict(self.config(Path(tmp)), resource_profile="unlimited"))

    def test_real_allocation_violation_is_retained_and_process_retired(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "mounted_images", return_value={"images": []}):
            root = Path(tmp)
            script = "import time;allocated=bytearray(64*1024*1024);time.sleep(30)"
            limits = dict(self.limits(), rss=32*1024*1024)
            instance = FakePreparation(self.config(root, script), limits)
            result = instance.execute()
            self.assertEqual("FAILED", result["status"])
            self.assertIn("RSS", result["error"])
            sample = result["samples"][-1]
            self.assertGreater(sample["rss"], limits["rss"])
            self.assertIn("RSS", sample["violations"][0])
            self.assertTrue(any(p["pid"] == instance.process.pid for p in sample["processes"]))
            self.assertIsNotNone(instance.process.returncode)
            self.assertFalse(result["cleanup"]["remaining"])

    def test_source_proven_optional_fit_warning_is_retained_as_false(self):
        fits = [{"piece": "boot-arg amfi_allow_any_signature", "fits": True, "proof": "read by the kernel"},
                {"piece": "boot-arg cs_enforcement_disable", "fits": False,
                 "proof": "the kernel does not read it as a boot-arg"}]
        events = [{"event": "warning", "message": "boot-arg cs_enforcement_disable does not fit this firmware (kept: inert here): the kernel does not read it as a boot-arg"}]
        assessment = module.assess_fits(fits, events)
        self.assertFalse(assessment["blocking_misfits"])
        self.assertFalse(assessment["optional_misfits"][0]["fits"])
        self.assertEqual("OPTIONAL_INERT_WARNING_UNQUALIFIED", assessment["optional_misfits"][0]["disposition"])
        self.assertFalse(fits[1]["fits"])
        self.assertTrue(module.assess_fits(fits, [])["blocking_misfits"])

    def test_required_unknown_or_incomplete_fit_evidence_never_passes(self):
        amfi = dict(piece="boot-arg amfi_allow_any_signature", fits=True, proof="read by the kernel")
        for extra in (dict(piece="boot-arg amfi_allow_any_signature", fits=False, proof="missing"),
                      dict(piece="unknown optional thing", fits=False, proof="not installed (disabled)"),
                      dict(piece="boot-arg cs_enforcement_disable", fits=False, proof="no decrypted kernelcache to check it against")):
            fits = [extra] if extra["piece"] == amfi["piece"] else [amfi, extra]
            assessment = module.assess_fits(fits, [])
            self.assertTrue(assessment["blocking_misfits"])
        self.assertTrue(module.assess_fits([dict(piece="other", fits=True, proof="ok")], [])["blocking_misfits"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            module.assess_fits([amfi, amfi], [])
        with self.assertRaisesRegex(ValueError, "malformed"):
            module.assess_fits([dict(amfi, fits=1)], [])

    def test_original_deadline_kills_owned_escaped_child_and_retains_logs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "mounted_images", return_value={"images": []}):
            root = Path(tmp)
            child_script = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
            script = ("import subprocess,sys,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                      "p=subprocess.Popen([sys.executable,'-c'," + repr(child_script) + "],start_new_session=True);"
                      "print(p.pid,flush=True);time.sleep(30)")
            before = time.monotonic()
            instance = FakePreparation(self.config(root, script), self.limits())
            result = instance.execute()
            self.assertLess(time.monotonic() - before, 4.5)
            self.assertEqual("FAILED", result["status"])
            self.assertIn("deadline", result["error"])
            child = int((instance.run / "stdout.jsonl").read_text())
            table = module.process_table(time.monotonic()+2)
            self.assertTrue(child not in table or table[child]["state"].startswith("Z"))
            self.assertFalse(result["cleanup"]["errors"])
            self.assertIsNotNone(instance.process.returncode)

    def test_failed_preflight_never_detaches_existing_images(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, "mounted_images") as mounts:
            instance = module.Supervisor(self.config(Path(tmp)))
            result = instance.execute()
            self.assertEqual("FAILED", result["status"])
            mounts.assert_not_called()

    def test_cancel_does_not_extend_original_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = module.Supervisor(self.config(Path(tmp)))
            original = instance.end
            instance.cancelled = True
            with self.assertRaises(InterruptedError):
                instance.check()
            self.assertEqual(original, instance.end)

    def test_observer_timeout_retires_its_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "pid"
            script = "import os,time;open(" + repr(str(pidfile)) + ",'w').write(str(os.getpid()));time.sleep(30)"
            with self.assertRaises(subprocess.TimeoutExpired):
                module.helper([sys.executable, "-c", script], time.monotonic()+1, timeout=.25)
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_mount_ownership_is_exact_root_and_preserves_unrelated_images(self):
        images = {"images": [{"image-path": "/tmp/pilot/work/source.dmg"},
                             {"image-path": "/tmp/pilot-other/user.dmg"}, {"image-path": "/Users/blake/source.dmg"}]}
        self.assertEqual(images["images"][:1], module.owned_images(images, Path("/tmp/pilot")))

    def synthetic(self, root):
        entry = root / "entry.json"
        entry.write_text('{"id":"n72ap-5G77a"}')
        instance = module.Supervisor(dict(self.config(root), staging=str(root / "prepared"), entry=str(entry),
                         seed="test-seed", guest_tool_sha256={"armv6.itpack": "f"*64}))
        instance.run.mkdir()
        out = root / "prepared"
        nand = out / "nand"
        nand.mkdir(parents=True)
        def file(name, data, readonly=False):
            p = out / name
            p.write_bytes(data)
            if readonly:
                p.chmod(0o400)
            return hashlib.sha256(data).hexdigest()
        nor = file("nor.bin", b"nor", True)
        gid = file("gid-blobs.bin", b"gid", True)
        identity = file("identity.json", b"{}")
        page = file("nand/page-1", b"page", True)
        listing = hashlib.sha256(("page-1 " + page + "\n").encode()).hexdigest()
        nand.chmod(0o500)
        lock = dict(board="n72ap", build="5G77a", entry={"id": "n72ap-5G77a", "content": {"id": "n72ap-5G77a"}}, boot_strategy="bootrom",
                    guest_package={"family": "n72-ios2", "itpack": {"sha256": "f"*64}},
                    tool={"built": {"guest tools": {"armv6.itpack": "f"*64}}},
                    inputs={"ipsw": {"sha1": "34a0a489605f34d6cc6c9954edcaaf9a050deedc"}},
                    identity={"seed": "test-seed", "sha256": identity},
                    fit=[{"piece": "boot-arg amfi_allow_any_signature", "fits": True, "proof": "read by the kernel"}],
                    outputs={"nor": {"path": "nor.bin", "sha256": nor}, "gid_blobs": {"path": "gid-blobs.bin", "sha256": gid},
                             "nand": {"path": "nand", "pages": 1, "listing_sha256": listing, "built_listing_sha256": listing}})
        (out / "device.lock.json").write_text(json.dumps(lock))
        (instance.run / "stdout.jsonl").write_text('{"event":"done","lock":"device.lock.json"}\n')
        return instance, lock

    def test_complete_synthetic_output_verification_and_corruption_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance, lock = self.synthetic(Path(tmp))
            instance.verify()
            self.assertEqual(1, instance.result["output"]["nand_files"])
            self.assertEqual("NOT_RUN", instance.result["output"]["runtime_acceptance"])
            page = Path(instance.config["staging"]) / "nand/page-1"
            page.chmod(0o600)
            page.write_bytes(b"evil")
            page.chmod(0o400)
            with self.assertRaisesRegex(ValueError, "listing hash"):
                instance.verify()

    def test_full_verifier_preserves_optional_warning_without_weakening_required_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance, lock = self.synthetic(Path(tmp))
            optional = dict(piece="boot-arg cs_enforcement_disable", fits=False,
                            proof="the kernel does not read it as a boot-arg")
            lock["fit"].append(optional)
            path = Path(instance.config["staging"]) / "device.lock.json"
            path.write_text(json.dumps(lock))
            events = [{"event": "warning", "message": "boot-arg cs_enforcement_disable does not fit this firmware (kept: inert here): the kernel does not read it as a boot-arg"},
                      {"event": "done", "lock": "device.lock.json"}]
            (instance.run / "stdout.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
            instance.verify()
            assessment = instance.result["output"]["fit_assessment"]
            self.assertFalse(assessment["blocking_misfits"])
            self.assertFalse(assessment["optional_misfits"][0]["fits"])
            lock["fit"][0]["fits"] = False
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "required or unreviewed"):
                instance.verify()

    def test_unreadable_done_and_symlink_outputs_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance, lock = self.synthetic(Path(tmp))
            (instance.run / "stdout.jsonl").write_text('{"event":"error"}\n')
            with self.assertRaisesRegex(ValueError, "done event"):
                instance.verify()
            nor = Path(instance.config["staging"]) / "nor.bin"
            nor.unlink()
            nor.symlink_to("gid-blobs.bin")
            with self.assertRaisesRegex(ValueError, "escapes staging"):
                instance.verify()


if __name__ == "__main__":
    unittest.main()
