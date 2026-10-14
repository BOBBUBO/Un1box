#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pixel adapter: bounded scoped readback before the unchanged owned cleanup."""
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time
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


class PixelAttempt(base.Attempt):
    def __init__(self, args):
        super().__init__(args)
        self.pixel_readback_done = False

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
        try:
            self.pixel_readback()
        finally:
            super().stop()

    def cleanup(self):
        try:
            self.pixel_readback()
        finally:
            super().cleanup()


if __name__ == "__main__":
    base.Attempt = PixelAttempt
    raise SystemExit(base.main())
