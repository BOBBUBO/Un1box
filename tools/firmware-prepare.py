#!/usr/bin/env python3
"""Single-use, bounded macOS n72/5G77a preparation supervisor (never pack or boot)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

GIB = 1024 ** 3
LIMITS = dict(total=600, work=540, term_end=585, rss=2 * GIB,
              disk=20 * GIB, free_disk=32 * GIB, free_memory=8 * GIB, processes=32)
CREATE03_LIMITS = dict(LIMITS, rss=4 * GIB, host_memory_floor=4 * GIB)


def assess_fits(fits, events):
    """Narrow n72 pilot policy: preserve optional misfits; never turn false into true.

    FitCheck.Log.check throws for required misfits but stores only piece/fits/proof.
    Pieces.checkBootArgs explicitly makes cs_enforcement_disable optional/inert.
    Unknown negatives still require review: the wire has no required/optional bit.
    """
    if not isinstance(fits, list) or not fits:
        raise ValueError("prepared fit evidence is absent")
    seen, optional, blocking = set(), [], []
    for fit in fits:
        if (not isinstance(fit, dict) or type(fit.get("fits")) is not bool or
                not isinstance(fit.get("piece"), str) or not isinstance(fit.get("proof"), str)):
            raise ValueError("malformed fit evidence")
        if fit["piece"] in seen:
            raise ValueError("duplicate fit evidence")
        seen.add(fit["piece"])
        if fit["fits"]:
            continue
        expected = (fit["piece"] == "boot-arg cs_enforcement_disable" and
                    fit["proof"] == "the kernel does not read it as a boot-arg")
        message = (fit["piece"] + " does not fit this firmware (kept: inert here): " + fit["proof"])
        warned = any(e == {"event": "warning", "message": message} for e in events)
        if expected and warned:
            optional.append(dict(fit, disposition="OPTIONAL_INERT_WARNING_UNQUALIFIED"))
        else:
            blocking.append(fit)
    amfi = next((f for f in fits if f["piece"] == "boot-arg amfi_allow_any_signature"), None)
    if not amfi or amfi["fits"] is not True:
        blocking.append(dict(piece="boot-arg amfi_allow_any_signature", fits=False,
                             proof="required positive fit missing or false"))
    return dict(optional_misfits=optional, blocking_misfits=blocking,
                policy="only source-proven optional cs_enforcement_disable warning admitted; other negatives held for review")


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".writing")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def regular(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("expected absolute regular file without symlink components: " + str(path))
    return path


def digest(path, check=lambda: None, algorithm="sha256"):
    path = regular(path)
    before = path.stat()
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        while True:
            check()
            block = stream.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("file changed while hashing: " + str(path))
    return h.hexdigest()


def helper(argv, deadline, timeout=3):
    """Bound every observer/detach and retire its process group on timeout."""
    remaining = min(timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("original deadline reached before observer")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = process.communicate(timeout=remaining)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=max(.05, min(1, deadline - time.monotonic())))
        raise
    if process.returncode:
        raise RuntimeError("observer failed: %r: %s" % (argv, err.decode(errors="replace")[:2000]))
    return out


def process_table(deadline):
    raw = helper(["ps", "-axo", "pid=,ppid=,pgid=,rss=,lstart=,stat=,comm="], deadline)
    result = {}
    for line in raw.decode().splitlines():
        fields = line.split(None, 10)
        if len(fields) < 11:
            raise ValueError("cannot parse process identity: " + line)
        pid, ppid, pgid, rss = map(int, fields[:4])
        result[pid] = dict(pid=pid, ppid=ppid, pgid=pgid, rss=rss * 1024,
                           identity=" ".join(fields[4:9]), state=fields[9], command=fields[10])
    return result


def mounted_images(deadline):
    return plistlib.loads(helper(["hdiutil", "info", "-plist"], deadline))


def owned_images(images, root):
    result = []
    for image in images.get("images", []):
        source = image.get("image-path", "")
        if source and Path(source).resolve().is_relative_to(root):
            result.append(image)
    return result


def validate_create_argv(argv, help_text):
    """Compare emitted flags to the *actual pinned binary*, not a newer source tree."""
    if len(argv) < 2 or argv[1] != "create":
        raise ValueError("expected create-only argv")
    offered = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", help_text))
    flags = argv[2::2]
    expected = {"--entry", "--ipsw", "--out", "--seed", "--guest-tools", "--cache"}
    if len(argv[2:]) != 12 or len(flags) != len(set(flags)) or set(flags) != expected:
        raise ValueError("unexpected create argument contract")
    if not set(flags) <= offered:
        raise ValueError("pinned create help does not offer: " + str(sorted(set(flags) - offered)))


class Supervisor:
    def __init__(self, config, limits=None):
        self.config = config
        profile = config.get("resource_profile", "original-2gib")
        if profile not in ("original-2gib", "n72-create03-4gib"):
            raise ValueError("unreviewed resource profile")
        self.limits = limits or (CREATE03_LIMITS if profile == "n72-create03-4gib" else LIMITS)
        self.start = time.monotonic()
        self.end = self.start + self.limits["total"]
        self.work_end = self.start + self.limits["work"]
        self.term_end = self.start + self.limits["term_end"]
        self.cancelled = False
        self.process = None
        self.known = {}
        self.last_disk_sample = -float("inf")
        self.owns_run = False
        self.root = Path(config["root"])
        run_name = config.get("run_name", "create-supervised-01")
        if not re.fullmatch(r"create-supervised-[0-9]{2}", run_name):
            raise ValueError("reviewed single-use create run name required")
        self.run = self.root / run_name
        self.result = dict(status="PREFLIGHT", original_start_monotonic=self.start,
                           original_work_deadline=self.work_end, original_total_deadline=self.end,
                           limits=self.limits, samples=[], pins={}, no_pack_or_guest_boot=True,
                           enforcement="sampled macOS watchdogs; not hard RSS/disk containment")

    def check(self):
        if self.cancelled:
            raise InterruptedError("cancelled; original deadlines unchanged")
        if time.monotonic() >= self.work_end:
            raise TimeoutError("original work deadline reached")

    def pin(self, path, expected):
        actual = digest(path, self.check)
        if actual != expected:
            raise ValueError("source hash mismatch: " + str(path))
        self.result["pins"][str(path)] = actual

    def admission(self):
        """Fresh actual macOS admission; observations remain evidence, not reservations."""
        page_data = helper(["vm_stat"], self.work_end).decode()
        page_size = int(re.search(r"page size of (\d+) bytes", page_data).group(1))
        free = int(re.search(r"Pages free:\s+(\d+)", page_data).group(1))
        inactive = int(re.search(r"Pages inactive:\s+(\d+)", page_data).group(1))
        available = (free + inactive) * page_size
        pressure = helper(["memory_pressure", "-Q"], self.work_end).decode()
        disk = shutil.disk_usage(self.root).free
        if disk < self.limits["free_disk"] or available < self.limits["free_memory"]:
            raise RuntimeError("fresh disk/free+inactive memory admission failed")
        self.result["admission"] = dict(free_disk=disk, free_plus_inactive=available,
                                        vm_stat=page_data, memory_pressure=pressure)

    def preflight(self):
        c = self.config
        if sys.platform != "darwin":
            raise ValueError("actual firmware preparation requires macOS")
        if c.get("execution_authorized") is not True or c.get("phase") != "create-only":
            raise ValueError("reviewed create-only execution allocation required")
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError("root must be absolute without symlinks")
        owner = self.root.stat()
        if owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) & 0o077:
            raise ValueError("pilot root must be private and user owned")
        self.run.mkdir(mode=0o700)  # single use; no overwrite/retry
        self.owns_run = True
        for name in ("staging", "cache", "service_state"):
            path = Path(c[name])
            if path.resolve() != path or not path.is_relative_to(self.root) or path == self.root or path.exists():
                raise ValueError("new output/cache/state path required under private root: " + str(path))
        paths = [Path(c[n]) for n in ("staging", "cache", "service_state")]
        if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
               for i, a in enumerate(paths) for b in paths[i+1:]):
            raise ValueError("output/cache/state paths overlap")
        for name in ("tool", "ipsw", "entry"):
            self.pin(c[name], c[name + "_sha256"])
            if any(Path(c[name]).is_relative_to(p) for p in paths):
                raise ValueError("input is inside writable preparation path")
        actual_help = helper([c["tool"], "create", "--help"], self.work_end, timeout=5)
        if hashlib.sha256(actual_help).hexdigest() != c["create_help_sha256"]:
            raise ValueError("pinned CLI help digest changed")
        validate_create_argv(self.argv(), actual_help.decode())
        self.result["actual_create_help"] = actual_help.decode()
        if Path(c["ipsw"]).stat().st_size != 291123491 or digest(c["ipsw"], self.check, "sha1") != "34a0a489605f34d6cc6c9954edcaaf9a050deedc":
            raise ValueError("selected IPSW exact size/SHA1 mismatch")
        entry = json.loads(Path(c["entry"]).read_text())
        if (entry["id"], entry["board"], entry["build"], entry["version"]) != ("n72ap-5G77a", "n72ap", "5G77a", "2.2"):
            raise ValueError("only reviewed n72ap-5G77a pilot is admitted")
        if entry["recipe"]["name"] != "n72" or entry["recipe"].get("options", {}).get("data_protection"):
            raise ValueError("unexpected helper-boot recipe")
        guest = Path(c["guest_tools"])
        if guest.resolve() != guest or not guest.is_dir():
            raise ValueError("guest-tools must be a real directory")
        if sorted(p.name for p in guest.iterdir()) != sorted(c["guest_tool_sha256"]):
            raise ValueError("guest-tools complete directory manifest mismatch")
        for name, expected in c["guest_tool_sha256"].items():
            if Path(name).name != name:
                raise ValueError("guest-tool path must be flat")
            self.pin(guest / name, expected)
        self.before = mounted_images(self.work_end)
        self.result["images_before"] = self.before
        if owned_images(self.before, self.root):
            raise ValueError("pilot root already owns a mounted image")
        table = process_table(self.work_end)
        resident = table.get(c["resident_avd_pid"])
        if not resident:
            raise ValueError("expected resident AVD absent before preparation")
        self.result["resident_avd_before"] = resident
        self.admission()
        for name in ("staging", "cache", "service_state"):
            Path(c[name]).mkdir(mode=0o700, parents=True, exist_ok=False)

    def argv(self):
        c = self.config
        return [c["tool"], "create", "--entry", c["entry"], "--ipsw", c["ipsw"], "--out", c["staging"],
                "--seed", c["seed"], "--guest-tools", c["guest_tools"], "--cache", c["cache"]]

    def observe(self, deadline):
        table = process_table(deadline)
        # Retain escaped/reparented descendants by PID+start identity after first observation.
        owned = {p for p, value in self.known.items() if p in table and table[p]["identity"] == value["identity"]}
        if self.process and self.process.pid in table:
            if not self.known or table[self.process.pid]["identity"] == self.known.get(self.process.pid, {}).get("identity"):
                owned.add(self.process.pid)
        changed = True
        while changed:
            added = {p for p, v in table.items() if v["ppid"] in owned or
                     (self.process and v["pgid"] == self.process.pid)} - owned
            changed = bool(added)
            owned.update(added)
        self.known.update({p: table[p] for p in owned})
        return [table[p] for p in owned if not table[p]["state"].startswith("Z")], table

    def sample(self):
        self.check()
        processes, table = self.observe(self.work_end)
        rss = sum(p["rss"] for p in processes)
        resident = table.get(self.config["resident_avd_pid"])
        value = dict(elapsed=time.monotonic()-self.start, rss=rss, processes=processes,
                     owned_process_count=len(processes), resident_avd=resident, violations=[])
        self.result["samples"].append(value)

        def fail(message):
            value["violations"].append(message)
            save(self.run / "RECEIPT.json", self.result)
            raise RuntimeError(message)

        # Save the exact violating measurement BEFORE throwing or starting cleanup.
        if len(processes) > self.limits["processes"]:
            fail("owned process count watchdog exceeded")
        if rss > self.limits["rss"]:
            fail("owned RSS watchdog exceeded")
        if any(re.search(r"qemu|LightTouchDevice|emulator", p["command"], re.I) for p in processes):
            fail("unexpected helper/guest emulator in no-boot preparation")
        if not resident or resident["identity"] != self.result["resident_avd_before"]["identity"]:
            fail("resident AVD changed/absent")
        save(self.run / "RECEIPT.json", self.result)
        if time.monotonic() - self.last_disk_sample >= 5:
            used = int(helper(["du", "-sk", str(self.root)], self.work_end).split()[0]) * 1024
            free = shutil.disk_usage(self.root).free
            self.last_disk_sample = time.monotonic()
            value.update(allocated=used, free_disk=free)
            if used > self.limits["disk"] or free < self.limits["free_disk"]:
                fail("new-root allocation/free-disk watchdog exceeded")
            if self.limits.get("host_memory_floor"):
                raw = helper(["vm_stat"], self.work_end).decode()
                page = int(re.search(r"page size of (\d+) bytes", raw).group(1))
                free_pages = int(re.search(r"Pages free:\s+(\d+)", raw).group(1))
                inactive_pages = int(re.search(r"Pages inactive:\s+(\d+)", raw).group(1))
                value.update(host_free_plus_inactive=(free_pages + inactive_pages) * page, vm_stat=raw)
                if value["host_free_plus_inactive"] < self.limits["host_memory_floor"]:
                    fail("host free+inactive memory reserve watchdog exceeded")
        save(self.run / "RECEIPT.json", self.result)

    def signal_owned(self, sig, deadline):
        processes, _ = self.observe(deadline)
        # Signal only observed matching identities; never a stale PID or unrelated group.
        for process in sorted(processes, key=lambda p: p["pid"] == self.process.pid):
            try:
                os.kill(process["pid"], sig)
            except ProcessLookupError:
                pass

    def cleanup(self):
        self.result["cleanup"] = dict(started=time.monotonic(), errors=[])
        errors = self.result["cleanup"]["errors"]
        if self.process:
            try:
                # Give firmwarekit's own cancellation/image-detach path first opportunity.
                if self.process.poll() is None:
                    self.process.terminate()
                cancel_end = min(self.term_end, time.monotonic() + self.limits["term_end"] - self.limits["work"])
                while time.monotonic() < cancel_end:
                    self.process.poll()
                    remaining, _ = self.observe(self.end)
                    if not remaining:
                        break
                    time.sleep(min(.25, cancel_end-time.monotonic()))
                self.signal_owned(signal.SIGKILL, self.end)
                self.process.wait(timeout=max(.05, min(2, self.end-time.monotonic())))
            except Exception as exc:
                errors.append("process cleanup: " + repr(exc))
        if not self.process:
            # A failed preflight never detaches anything it did not create.
            self.result["cleanup"]["ended"] = time.monotonic()
            return
        try:
            images = mounted_images(self.end)
            self.result["cleanup"]["images_before_detach"] = images
            for image in owned_images(images, self.root):
                devices = [e["dev-entry"] for e in image.get("system-entities", []) if e.get("dev-entry")]
                if not devices or not all(re.fullmatch(r"/dev/disk\d+(?:s\d+)*", d) for d in devices):
                    raise ValueError("owned mount has no safe exact device identity")
                helper(["hdiutil", "detach", devices[0]], self.end, timeout=5)
            after = mounted_images(self.end)
            self.result["cleanup"]["images_after"] = after
            if owned_images(after, self.root):
                errors.append("owned disk-image attachment remains")
            if self.process:
                remaining, table = self.observe(self.end)
                self.result["cleanup"]["remaining"] = remaining
                if remaining:
                    errors.append("owned process remains")
                resident = table.get(self.config["resident_avd_pid"])
                self.result["resident_avd_after"] = resident
                if not resident or resident["identity"] != self.result["resident_avd_before"]["identity"]:
                    errors.append("resident AVD identity changed/absent")
        except Exception as exc:
            errors.append("mount/final process verification: " + repr(exc))
        self.result["cleanup"]["ended"] = time.monotonic()

    def verify(self):
        c = self.config
        lock_path = Path(c["staging"]) / "device.lock.json"
        lock = json.loads(regular(lock_path).read_text())
        if (lock["board"], lock["build"], lock["entry"]["id"], lock["boot_strategy"]) != ("n72ap", "5G77a", "n72ap-5G77a", "bootrom"):
            raise ValueError("prepared board/build/boot contract mismatch")
        if lock["guest_package"]["family"] != "n72-ios2" or lock["guest_package"]["itpack"]["sha256"] != c["guest_tool_sha256"]["armv6.itpack"]:
            raise ValueError("prepared guest family/package mismatch")
        if lock["tool"]["built"]["guest tools"] != c["guest_tool_sha256"]:
            raise ValueError("prepared guest-tool provenance mismatch")
        if lock["inputs"]["ipsw"]["sha1"] != "34a0a489605f34d6cc6c9954edcaaf9a050deedc":
            raise ValueError("prepared input provenance mismatch")
        if lock["entry"].get("content") != json.loads(Path(c["entry"]).read_text()):
            raise ValueError("prepared catalog content mismatch")
        if lock["identity"]["seed"] != c["seed"]:
            raise ValueError("prepared identity seed mismatch")
        output = Path(c["staging"])
        def output_path(name):
            p = output / name
            if not p.resolve().is_relative_to(output) or p.resolve() != p:
                raise ValueError("prepared output escapes staging")
            return p
        checked = {}
        for key in ("nor", "gid_blobs"):
            record = lock["outputs"][key]
            p = output_path(record["path"])
            actual = digest(p, self.check)
            if actual != record["sha256"] or p.stat().st_mode & 0o222:
                raise ValueError("prepared boot output hash/mode mismatch: " + key)
            checked[key] = actual
        if digest(output / "identity.json", self.check) != lock["identity"]["sha256"]:
            raise ValueError("prepared identity hash mismatch")
        nand = output_path(lock["outputs"]["nand"]["path"])
        if not nand.is_dir() or nand.stat().st_mode & 0o222:
            raise ValueError("NAND root is absent or writable")
        files = []
        for directory, directories, names in os.walk(nand, followlinks=False):
            self.check()
            for name in directories + names:
                p = Path(directory) / name
                if p.is_symlink() or (not p.is_dir() and not p.is_file()):
                    raise ValueError("unexpected NAND symlink/special file")
                if p.stat().st_mode & 0o222:
                    raise ValueError("NAND content remains writable")
            files += [str((Path(directory) / name).relative_to(nand)) for name in names]
        if not files:
            raise ValueError("prepared NAND is empty")
        listing = hashlib.sha256()
        total = 0
        for name in sorted(files):
            p = nand / name
            h = digest(p, self.check)
            total += p.stat().st_size
            listing.update((name + " " + h + "\n").encode())
        expected = lock["outputs"]["nand"]
        if (listing.hexdigest() != expected["listing_sha256"] or listing.hexdigest() != expected["built_listing_sha256"]
                or len(files) != expected["pages"]):
            raise ValueError("complete NAND listing hash mismatch")
        events = []
        with (self.run / "stdout.jsonl").open() as stream:
            for line in stream:
                self.check()
                events.append(json.loads(line))
        if not events or events[-1] != {"event": "done", "lock": "device.lock.json"} or any(e.get("event") == "error" for e in events):
            raise ValueError("missing exact final done event or preparer error")
        fits = lock.get("fit", [])
        assessment = assess_fits(fits, events)
        self.result["output"] = dict(lock_sha256=digest(lock_path, self.check), boot_hashes=checked,
             nand_listing_sha256=listing.hexdigest(), nand_files=len(files), nand_logical_bytes=total,
             pages=expected.get("pages"), fits=fits, fit_assessment=assessment,
             warnings=[e for e in events if e.get("event") == "warning"],
             status="CONTENT_VERIFIED_REVIEW_REQUIRED", runtime_acceptance="NOT_RUN")
        if assessment["blocking_misfits"]:
            raise ValueError("required or unreviewed prepared fit failures require review")

    def execute(self):
        try:
            self.preflight()
            self.check()
            self.result["argv"] = self.argv()
            self.result["status"] = "RUNNING"
            save(self.run / "RECEIPT.json", self.result)
            env = dict(os.environ, LTM_STATE_DIR=self.config["service_state"])
            with (self.run / "stdout.jsonl").open("xb") as stdout, (self.run / "stderr.log").open("xb") as stderr:
                self.process = subprocess.Popen(self.argv(), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                                env=env, start_new_session=True)
                while self.process.poll() is None:
                    self.sample()
                    time.sleep(min(.5, max(0, self.work_end-time.monotonic())))
            self.result["returncode"] = self.process.returncode
            if self.process.returncode != 0:
                raise RuntimeError("preparer exited unsuccessfully")
            self.sample()
            remaining, _ = self.observe(self.work_end)
            if remaining:
                raise RuntimeError("preparer exited but owned descendants remain")
            self.verify()
            for path, expected in list(self.result["pins"].items()):
                self.pin(path, expected)
            self.result["status"] = "CONTENT_VERIFIED_REVIEW_REQUIRED"
        except BaseException as exc:
            self.result.update(status="FAILED", error=type(exc).__name__ + ": " + str(exc))
        finally:
            if self.owns_run:
                self.cleanup()
                if self.result["cleanup"]["errors"]:
                    self.result["status"] = "FAILED"
                self.result["seconds"] = time.monotonic()-self.start
                save(self.run / "RECEIPT.json", self.result)
        return self.result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--authorize-config-sha256", required=True,
                        help="Exact reviewed execution config digest; no auto-retry or deadline extension.")
    args = parser.parse_args()
    if digest(args.config) != args.authorize_config_sha256:
        parser.error("reviewed execution config hash mismatch")
    config = json.loads(Path(args.config).read_text())
    if digest(Path(__file__).resolve()) != config["supervisor_sha256"]:
        parser.error("reviewed supervisor source hash mismatch")
    instance = Supervisor(config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: setattr(instance, "cancelled", True))
    result = instance.execute()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "CONTENT_VERIFIED_REVIEW_REQUIRED" else 1


if __name__ == "__main__":
    sys.exit(main())
