"""LLDB command for a guest proc/task/pmap snapshot after a frame capture.

Inside LLDB: command script import /path/to/guestprocs.py
Then: lt-guest-procs --kernel KERNEL --xnu QEMU/imgtools/lldb/xnu.py
                    --connect connect://127.0.0.1:PORT --json OUT
Attaching pauses the guest. Use only after the timed window, before clean Stop.
The existing qemu-ios helper derives offsets from the matching guest kernel.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex

import lldb


def snapshot(debugger, command, context, result, internal_dict):
    ap = argparse.ArgumentParser(prog="lt-guest-procs", description=__doc__)
    ap.add_argument("--kernel", type=Path, required=True)
    ap.add_argument("--xnu", type=Path, required=True)
    ap.add_argument("--connect", required=True)
    ap.add_argument("--json", type=Path, required=True)
    ap.add_argument("--thread", type=lambda s: int(s, 0), action="append", default=[],
                    help="also read this guest thread's task owner (repeatable, accepts hex)")
    try:
        args = ap.parse_args(shlex.split(command))
        if len(args.thread) > 4096 or any(t <= 0 or t > 0xffffffff or t & 3
                                          for t in args.thread):
            ap.error("thread addresses must be aligned ARM32 pointers, at most 4096")
    except SystemExit:
        result.SetError("invalid guest snapshot arguments")
        return
    process = None
    report = None
    failure = None
    try:
        if args.json.exists():
            raise ValueError("snapshot output already exists")
        target = debugger.CreateTargetWithFileAndArch(str(args.kernel), "armv6-apple-ios")
        if not target.IsValid():
            raise ValueError("cannot create ARMv6 kernel target")
        debugger.SetSelectedTarget(target)
        debugger.SetAsync(False)
        error = lldb.SBError()
        process = target.ConnectRemote(debugger.GetListener(), args.connect, "gdb-remote", error)
        if error.Fail() or not process.IsValid() or process.GetState() != lldb.eStateStopped:
            raise ValueError("cannot stop guest for snapshot: " + str(error))
        target.SetModuleLoadAddress(target.GetModuleAtIndex(0), 0)
        spec = importlib.util.spec_from_file_location("lt_xnu_snapshot", args.xnu)
        xnu = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(xnu)
        rows = xnu.procs()
        if not rows or any(row[4] is None or row[5] is None for row in rows):
            raise ValueError("incomplete proc/pmap snapshot: " + repr(xnu.OFF))
        registers = {name: xnu._reg(name) for name in
                     ("TTBR0_EL1", "TTBR1_EL1", "TTBCR", "CONTEXTIDR_EL1", "TPIDRPRW", "pc", "cpsr")}
        if any(registers[k] is None for k in ("TTBR0_EL1", "TTBR1_EL1", "TTBCR")):
            raise ValueError("missing translation registers")
        n = registers["TTBCR"] & 7
        mask = ~((1 << (14 - n)) - 1)
        if not any(row[5] == registers["TTBR0_EL1"] & mask for row in rows):
            raise ValueError("active TTBR0 does not match a process pmap")
        if not any(row[2] == "kernel_task" and
                   row[5] & ~0x3fff == registers["TTBR1_EL1"] & ~0x3fff for row in rows):
            raise ValueError("kernel pmap does not match TTBR1")
        owners = {}
        for p, pid, name, task, pmap, ttb in rows:
            owners.setdefault(task, []).append(dict(proc=p, pid=pid, name=name))
        threads = []
        if args.thread:
            if "thread_task" not in xnu.OFF or "task_proc" not in xnu.OFF:
                raise ValueError("matching kernel did not supply thread/task offsets")
            for thread in sorted(set(args.thread)):
                task = xnu._u32(thread + xnu.OFF["thread_task"])
                bsd_proc = xnu._u32(task + xnu.OFF["task_proc"]) if task else None
                candidates = owners.get(task, [])
                matches = [p for p in candidates if p["proc"] == bsd_proc]
                owner = matches[0] if len(matches) == 1 else None
                status = ("matched" if owner else
                          "bsd-proc-mismatch" if candidates else
                          "task-not-in-proc-snapshot" if task else "unreadable-task")
                threads.append(dict(thread=thread, task=task, bsd_proc=bsd_proc,
                                    owner=owner, owner_candidates=candidates, status=status))
        report = dict(kernel_sha256=hashlib.sha256(args.kernel.read_bytes()).hexdigest(),
                      xnu_helper_sha256=hashlib.sha256(args.xnu.read_bytes()).hexdigest(),
                      offsets=xnu.OFF, registers=registers, ttbr0_alignment_bits=14 - n,
                      processes=[dict(proc=p, pid=pid, name=name, task=task, pmap=pmap, ttbr0=ttb)
                                 for p, pid, name, task, pmap, ttb in rows],
                      threads=threads)
    except Exception as error:
        failure = str(error)
    finally:
        if process is not None and process.IsValid() and process.GetState() != lldb.eStateDetached:
            detached = process.Detach()
            if detached.Fail():
                failure = "guest detach failed: " + str(detached) + ("; " + failure if failure else "")
            elif report is not None:
                report["detached"] = True
    if failure:
        result.SetError(failure)
        return
    try:
        with args.json.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
        for row in report["processes"]:
            result.AppendMessage(f"{row['pid']:5} {row['name']:18} ttbr0=0x{row['ttbr0']:08x}")
        for row in report["threads"]:
            label = row["owner"]["name"] if row["status"] == "matched" else row["status"]
            result.AppendMessage(f"thread 0x{row['thread']:08x}: {label}")
        result.AppendMessage("SNAPSHOT_COMPLETE; guest detached")
    except OSError as error:
        result.SetError(str(error))


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f guestprocs.snapshot lt-guest-procs")
