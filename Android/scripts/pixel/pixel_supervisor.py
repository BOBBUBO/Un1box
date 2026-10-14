#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pixel adapter: bounded scoped readback before the unchanged owned cleanup."""
import argparse
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time
import sys
import memory_monitor
from diagnostic_control import Capture

spec = importlib.util.spec_from_file_location("base_retry", Path(__file__).resolve().parents[1] / "supervise_16k_retry.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def capture_child(adb, binding, output):
    # A stalled local file or ADB read cannot keep the parent from Stop/cleanup.
    os.setsid()
    Capture(adb, binding, output).collect()


def bounded_capture(adb, binding, output, now=time.monotonic_ns, process_factory=None):
    begin = now()
    end = min(begin + 10_000_000_000, binding["originNs"] + 810_000_000_000)
    if end - begin < 300_000_000:
        return {"captureStarted": False, "expired": True, "guestAcceptancePassed": False}
    factory = process_factory or multiprocessing.get_context("fork").Process
    child = factory(target=capture_child, args=(adb, binding, output))
    child.start()
    identity = base.process_identity(child.pid)
    deadline = end - 200_000_000  # Reserve child disposal before the original gate.
    child.join(timeout=max(0, (deadline - now()) / 1e9))
    forced = child.is_alive()
    if forced and base.process_identity(child.pid) == identity and identity is not None:
        try:
            # The child creates its own group; do not signal the parent's group.
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, signal.SIGKILL)
            else:
                os.kill(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.join(timeout=max(0, (end - now()) / 1e9))
    return {"captureStarted": True, "captureChildPid": child.pid, "forcedCaptureDisposal": forced,
            "captureChildExit": child.exitcode, "captureChildStillAlive": child.is_alive(),
            "beginNs": begin, "limitNs": end, "endedNs": now(), "guestAcceptancePassed": False}


PIXEL_MEMORY_MB = None


def pixel_options(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pixel-memory-mb", type=int, choices=(4096, 8192))
    args, remaining = parser.parse_known_args(argv)
    if args.pixel_memory_mb is not None and any(x == "--memory-mb" or x.startswith("--memory-mb=") for x in remaining):
        raise ValueError("Use only one memory allocation flag")
    return args.pixel_memory_mb, remaining


class PixelAttempt(base.Attempt):
    def __init__(self, args):
        super().__init__(args)
        self.pixel_readback_done = False
        self.pixel_monitor = None
        self.pixel_monitor_stop = multiprocessing.get_context("fork").Event()
        self.pixel_monitor_identity = None
        self.pixel_guest_disable = multiprocessing.get_context("fork").Event()
        self.pixel_guest_idle = multiprocessing.get_context("fork").Event()
        if PIXEL_MEMORY_MB is not None:
            args.memory_mb = PIXEL_MEMORY_MB

    def launch(self):
        super().launch()
        self.memory_phase("launch")
        ticks = memory_monitor.process_ticks(self.avd.pid)
        if ticks is None:
            raise RuntimeError("Owned AVD wrapper identity unavailable for monitor")
        # Binding uses the same original process origin as capture_origin.py.
        origin = memory_monitor.process_ticks(os.getpid()) * 1_000_000_000 // os.sysconf("SC_CLK_TCK")
        deadline = min(int(self.deadline * 1e9), origin + 900_000_000_000)
        self.pixel_monitor = multiprocessing.get_context("fork").Process(target=memory_monitor.monitor,
            args=(self.args.adb, str(self.output), self.avd.pid, ticks, origin, deadline, self.pixel_monitor_stop, self.pixel_guest_disable, self.pixel_guest_idle))
        self.pixel_monitor.start()
        self.pixel_monitor_identity = base.process_identity(self.pixel_monitor.pid)
        self.write("memory-monitor.json", {"pid": self.pixel_monitor.pid, "ownerPid": self.avd.pid,
            "ownerStartTicks": ticks, "originNs": origin, "deadlineNs": deadline,
            "requestedCadenceSeconds": 2, "guestAcceptancePassed": False})

    def memory_phase(self, phase):
        try:
            (self.output / "memory-phase.txt").write_text(phase)
            with (self.output / "memory-lifecycle.jsonl").open("a") as stream:
                stream.write(json.dumps({"phase": phase, "host": memory_monitor.host_sample()}) + "\n")
        except OSError:
            pass  # Observations cannot suppress semantic Stop or owned disposal.

    def retire_monitor(self):
        self.pixel_monitor_stop.set()
        if self.pixel_monitor is None:
            return
        self.pixel_monitor.join(timeout=min(1, max(0, self.deadline - time.monotonic())))
        if self.pixel_monitor.is_alive() and self.pixel_monitor_identity is not None and base.process_identity(self.pixel_monitor.pid) == self.pixel_monitor_identity:
            try:
                if os.getpgid(self.pixel_monitor.pid) == self.pixel_monitor.pid:
                    os.killpg(self.pixel_monitor.pid, signal.SIGKILL)
                else:
                    self.pixel_monitor.kill()
            except ProcessLookupError:
                pass
        self.pixel_monitor.join(timeout=min(.2, max(0, self.deadline - time.monotonic())))
        self.write("memory-monitor-terminal.json", {"exit": self.pixel_monitor.exitcode,
            "stillAlive": self.pixel_monitor.is_alive(), "guestAcceptancePassed": False})

    def pixel_readback(self):
        if self.pixel_readback_done or self.avd is None:
            return
        self.pixel_readback_done = True
        try:
            raw = (self.output / "clock-context.json").read_bytes()
            context = json.loads(raw)
            binding = json.loads((self.output / "pixel-bound.json").read_text())
            if binding["originToken"] != hashlib.sha256(raw).hexdigest() or binding["originNs"] != context["originNs"]:
                raise ValueError("Bound original clock differs")
            if context["supervisorStartTicks"] * 1_000_000_000 // context["clockTicksPerSecond"] != context["originNs"]:
                raise ValueError("Original process clock was backdated/reset")
            outcome = bounded_capture(self.args.adb, binding, self.output / "pixel-precleanup")
            self.write("pixel-precleanup-process.json", outcome)
        except Exception as error:
            try:
                self.write("pixel-readback-unavailable.json", {"error": str(error), "guestAcceptancePassed": False,
                           "cleanupDependsOnCapture": False})
            except Exception:
                pass  # Receipt failure cannot suppress owned disposal.

    def stop(self):
        self.memory_phase("stop-requested")
        try:
            self.pixel_readback()
        finally:
            super().stop()

    def cleanup(self):
        try:
            self.pixel_readback()
        finally:
            try:
                self.memory_phase("cleanup-before-owned-disposal")
                self.pixel_guest_disable.set()
                if self.pixel_monitor is not None and not self.pixel_guest_idle.wait(
                        timeout=min(.9, max(0, self.deadline - time.monotonic()))):
                    self.retire_monitor()  # Stalled observation cannot hold disposal.
                    self.write("memory-monitor-gap.json", {"continuousHostDuringCleanup": False,
                        "reason": "Guest observation did not acknowledge bounded cleanup barrier"})
            finally:
                try:
                    super().cleanup()
                finally:
                    try:
                        self.memory_phase("cleanup-after-owned-disposal")
                    finally:
                        self.retire_monitor()


if __name__ == "__main__":
    PIXEL_MEMORY_MB, sys.argv[1:] = pixel_options(sys.argv[1:])
    base.Attempt = PixelAttempt
    raise SystemExit(base.main())
