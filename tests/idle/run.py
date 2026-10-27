#!/usr/bin/env python3
"""The app's idle work, checked as text: a loop that wakes for nothing is battery.

The emulation itself is most of the heat while the OS boots, but the app has two
loops of its own that used to pay for silence:

  audio pull    asks the engine for samples in a loop. While the guest plays
                nothing every answer is zero, and the question used to be asked
                a hundred times a second -- a JNI call, a wake and a lock each
                time -- for the whole of a boot. The wait grows while it stays
                idle and drops back the moment a packet arrives, so the first
                sound is one short wait late at most.
  status poll   reads the worker's state twice a second for as long as the app is
                open. It used to start a thread per turn: a stack, a wake and a
                teardown, twice a second, for the entire life of the guest. It
                reuses one thread now, shut down with the activity.

Both are text, so both are checked here: no build, no device, no CI run.

  python3 tests/idle/run.py [-v]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = "Android/app/src/main/java/org/un1box/android/EngineService.kt"
ACTIVITY = "Android/app/src/main/java/org/un1box/android/MainActivity.kt"


class Failure(RuntimeError):
    pass


def read(relative):
    path = ROOT / relative
    if not path.is_file():
        raise Failure(f"{relative} is missing")
    return path.read_text(errors="ignore")


def require(condition, message):
    if not condition:
        raise Failure(message)


def audio_pull_backs_off():
    """The pull loop must not ask a hundred times a second for silence it will not get."""
    service = read(SERVICE)
    require("AUDIO_IDLE_MIN_MS" in service and "AUDIO_IDLE_MAX_MS" in service,
            "the audio pull loop has no idle backoff: it asks a hundred times a second for silence")
    require("var idleWaitMs = AUDIO_IDLE_MIN_MS" in service,
            "the pull loop has no idle wait to grow, so an idle guest still costs a hundred wakes a second")
    loop = service.split("var idleWaitMs", 1)[1]
    require("idleWaitMs = minOf(idleWaitMs * 2, AUDIO_IDLE_MAX_MS)" in loop,
            "the audio wait never grows, so an idle guest still costs a hundred wakes a second")
    require("Thread.sleep(idleWaitMs.toLong())" in loop,
            "the pull loop sleeps a fixed amount instead of its idle wait")
    after_packet = loop.split("if (count > 0)", 1)
    require(len(after_packet) > 1 and "idleWaitMs = AUDIO_IDLE_MIN_MS" in after_packet[1][:400],
            "a packet does not reset the audio wait, so sound would start late for ever")


def status_poll_reuses_one_thread():
    """A poll that runs until the activity dies must not create a thread per turn."""
    activity = read(ACTIVITY)
    require('"status-poll"' in activity,
            "the status poll has no thread of its own, so every turn creates and tears one down")
    require("pollThread" in activity and "pollThread.execute" in activity,
            "the status poll does not run on its own thread")
    require('}, "worker-status").start()' not in activity,
            "the poll still starts a new worker-status thread each time")
    require("pollThread.shutdownNow()" in activity,
            "the poll thread is never shut down with the activity, so it outlives it")


CHECKS = [
    ("audio: the pull loop backs off while the guest is silent", audio_pull_backs_off),
    ("android: the status poll reuses one thread", status_poll_reuses_one_thread),
]


class Report:
    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, name, ok, detail=""):
        self.rows.append((name, ok, detail))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and self.verbose else ""))
        return ok

    def run(self, name, action):
        try:
            return self.add(name, True, action() or "")
        except Failure as failure:
            return self.add(name, False, str(failure))
        except Exception as failure:  # a crash in a check is a failed check
            return self.add(name, False, f"{type(failure).__name__}: {failure}")

    @property
    def failed(self):
        return [row for row in self.rows if not row[1]]


def main():
    report = Report("-v" in sys.argv)
    for name, check in CHECKS:
        report.run(name, check)
    print()
    if report.failed:
        # Also as workflow commands: these checks run with continue-on-error in CI, where a failure is not
        # allowed to withhold an APK -- so the annotation is the only place it is visible.
        for name, _, detail in report.failed:
            print(f"::error title={name}::{detail}")
        print(f"{len(report.failed)} of {len(report.rows)} checks failed:")
        for name, _, detail in report.failed:
            print(f"  {name}: {detail.splitlines()[0] if detail else ''}")
        return 1
    print(f"all {len(report.rows)} idle checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
