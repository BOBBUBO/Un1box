#!/usr/bin/env python3
"""Flight Control gameplay fps for an APK: install it, boot to the lock screen, unlock,
launch the game from the first slot of Home page 2, tap Play and measure delivered fps.
usage: fcbench.py APK [SECONDS] (ANDROID_SERIAL set; Flight Control installed in the guest,
first icon on Home page 2)."""
import os, subprocess, sys, time
D = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(D, "inappfps.py")).read().split("ap = argparse")[0])
apk = sys.argv[1]; secs = sys.argv[2] if len(sys.argv) > 2 else "30"
subprocess.run(["bash", os.path.join(D, "swapapk.sh"), apk], check=True)
subprocess.run([sys.executable, os.path.join(D, "inappfps.py"), "--boot-only"], check=True)
def awake(rect): return sum(avg(rect, .1, .1, .9, .9)) >= 15
for attempt in range(6):
    rect = guest_rect()
    if not awake(rect):
        tap_text("Power"); time.sleep(3); continue  # the layout shifts once awake: re-measure
    if is_lock(rect):
        sh("input", "swipe", *gpt(rect, .2, .89), *gpt(rect, .95, .89), "400"); time.sleep(3); continue
    break
else:
    sys.exit("could not unlock the guest")
sh("input", "swipe", *gpt(rect, .9, .5), *gpt(rect, .15, .5), "300"); time.sleep(3)  # Home page 1 -> 2
sh("input", "tap", *gpt(rect, .143, .127)); time.sleep(18)  # Flight Control
sh("input", "tap", *gpt(rect, .153, .629)); time.sleep(4)   # PLAY!
subprocess.run([sys.executable, os.path.join(D, "gamefps.py"), secs])
if os.environ.get("FC_SHOT"):
    with open(os.environ["FC_SHOT"], "wb") as f:
        f.write(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)
