#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Capture a new allocated Pixel supervisor's original process clock exactly once."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def capture(attempt, pid, session, avd):
    out = Path(attempt).resolve()
    wrapper = Path(__file__).with_name("pixel_supervisor.py").resolve()
    before_fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    if os.fsencode(wrapper) not in args:
        raise ValueError("Wrong supervisor executable")
    for option, value in ((b"--output", os.fsencode(out)), (b"--avd", os.fsencode(avd)),
                          (b"--expected-page-bytes", b"16384")):
        if option not in args or args[args.index(option) + 1] != value:
            raise ValueError("Supervisor allocation differs")
    if any((p / ".git").exists() for p in (out, *out.parents)):
        raise ValueError("Private attempt must be outside Git")
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[19] != before_fields[19]:
        raise ValueError("Supervisor PID reused during origin capture")
    ticks, hz = int(fields[19]), os.sysconf("SC_CLK_TCK")
    origin_ns = ticks * 1_000_000_000 // hz
    context = {"schema": 2, "supervisorPid": pid, "supervisorStartTicks": ticks,
               "clockTicksPerSecond": hz, "originNs": origin_ns,
               "conservativeProcessOriginMonotonic": origin_ns / 1e9,
               "persistentExecSession": session, "avd": avd,
               "uiGateNs": origin_ns + 240_000_000_000,
               "readbackGateNs": origin_ns + 810_000_000_000,
               "stopGateNs": origin_ns + 840_000_000_000,
               "hardGateNs": origin_ns + 900_000_000_000,
               "capturedAtNs": time.monotonic_ns(), "clockOriginReset": False,
               "wrapperSha256": hashlib.sha256(wrapper.read_bytes()).hexdigest()}
    target = out / "clock-context.json"
    with target.open("x") as stream:
        stream.write(json.dumps(context, indent=2) + "\n")
    return context


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--attempt", required=True)
    p.add_argument("--supervisor-pid", type=int, required=True)
    p.add_argument("--exec-session", type=int, required=True)
    p.add_argument("--avd", required=True)
    a = p.parse_args()
    print(json.dumps(capture(a.attempt, a.supervisor_pid, a.exec_session, a.avd), indent=2))
