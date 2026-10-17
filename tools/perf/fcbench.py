#!/usr/bin/env python3
"""Game fps for an APK: install it, boot to the lock screen, unlock, open Home page 2, run a
game's launch steps and measure delivered fps with gamefps.py.
usage: fcbench.py APK [SECONDS] [GAME] (ANDROID_SERIAL set; the game installed in the guest)
GAME: a preset below; each follows one phone's Home page 2 (Poco: Flight Control, Pocket God; Pixel:
Doodle Jump, JellyCar). Check what was measured with FC_SHOT=file.png; FC_SERIES=1 prints fps per second.
Guest coordinates are fractions of the guest screen, so they work on any phone."""
import os, subprocess, sys, time
D = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(D, "inappfps.py")).read().split("ap = argparse")[0])
GAMES = {
    # (tap x, y) or seconds to wait
    "flightcontrol": [(.143, .127), 18, (.153, .629), 4],  # icon, PLAY! (Poco layout)
    "doodlejump": [(.143, .127), 22],                      # main menu (Pixel layout)
    "jellycar": [(.397, .127), 20, (.294, .497), 8],       # icon, dismiss the push prompt (Pixel layout)
}
apk = sys.argv[1]; secs = sys.argv[2] if len(sys.argv) > 2 else "30"
steps = GAMES[sys.argv[3] if len(sys.argv) > 3 else "flightcontrol"]
subprocess.run(["bash", os.path.join(D, "swapapk.sh"), apk], check=True)
subprocess.run([sys.executable, os.path.join(D, "inappfps.py"), "--boot-only"], check=True)
# One Power tap wakes the guest to the lock screen, which re-sleeps within ~5 s, so poll for it and
# swipe at once; tapping Power again before the wake shows (slow on the Poco) puts it back to sleep.
# The guest view no longer moves when it wakes, so the layout is read once.
rect = guest_rect(); _, power = find(ui(), "Power")
def unlocked():
    for _ in range(3):
        if sum(avg(rect, .1, .1, .9, .9)) < 15:
            sh("input", "tap", *map(str, power)); t = time.monotonic()
            while time.monotonic()-t < 8 and sum(avg(rect, .1, .1, .9, .9)) < 15: pass
        for _ in range(3):
            if not is_lock(rect): break
            sh("input", "swipe", *gpt(rect, .2, .89), *gpt(rect, .95, .89), "400"); time.sleep(1.5)
        if not is_lock(rect) and sum(avg(rect, .1, .1, .9, .9)) >= 15: return True
    return False
if not unlocked(): sys.exit("could not unlock the guest")
sh("input", "swipe", *gpt(rect, .9, .5), *gpt(rect, .15, .5), "300"); time.sleep(3)  # Home page 1 -> 2
for step in steps:
    if isinstance(step, tuple): sh("input", "tap", *gpt(rect, *step))
    else: time.sleep(step)
subprocess.run([sys.executable, os.path.join(D, "gamefps.py"), secs] + (["--series"] if os.environ.get("FC_SERIES") else []))
if os.environ.get("FC_SHOT"):
    with open(os.environ["FC_SHOT"], "wb") as f:
        f.write(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)
