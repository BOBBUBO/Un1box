#!/bin/bash
# Stop the in-app guest cleanly, then install an APK. usage: swapapk.sh APK (ANDROID_SERIAL set)
set -u
D=$(dirname "$0")
PERF_DIR="$D" python3 - <<'PY'
import time, sys, os
__file__ = sys.argv[0] if len(sys.argv) > 0 and sys.argv[0] else "x"
exec(open(os.path.join(os.environ.get("PERF_DIR", "."), "inappfps.py")).read().split("ap = argparse")[0])
sh("input", "keyevent", "KEYCODE_WAKEUP"); sh("am", "start", "-n", f"{PKG}/.MainActivity"); time.sleep(2)
st = state()
if st not in ("stopped", "?"):
    if st == "paused": tap_id("resume_overlay") or tap_text("Resume"); time.sleep(2)
    stop(); t = time.monotonic()
    while state() != "stopped" and time.monotonic() - t < 90: time.sleep(2)
print("state", state(), flush=True)
PY
adb install -r "$1" | tail -1
