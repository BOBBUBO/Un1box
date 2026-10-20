#!/usr/bin/env python3
"""Game fps for an APK: install it, boot to the lock screen, unlock, open Home page 2, run a
game's launch steps and measure delivered fps with gamefps.py.
usage: fcbench.py APK [SECONDS] [GAME] (ANDROID_SERIAL set; the game installed in the guest)
GAME: a preset below, default jetpack (Jetpack Joyride's title); each follows one phone's Home page 2 (Poco since
2026-10-20: Doodle Jump, Flight Control, JellyCar, Jetpack / Bike or Die!, Cut the Rope, N.O.V.A., Asphalt 5; Pixel:
Doodle Jump, JellyCar). Check what was measured with FC_SHOT=file.png; FC_SERIES=1 prints fps per second.
FC_STEPS=DIR saves the screen before each tap as DIR/stepNN.png.
FC_ENV="KEY=VALUE ..." replaces files/debug-env with exactly those lines for this run (debug APK).
FC_FRAMES=chart.png also logs every guest frame (LT_FRAME_LOG) and reports framestats.py's ceiling,
dips and hitches for the same window, with a chart.
SECONDS=0 launches without sampling fps (useful for LT Bench's static results screen).
Guest coordinates are fractions of the guest screen, so they work on any phone."""
import os, subprocess, sys, time
D = os.path.dirname(os.path.abspath(__file__))
exec(open(os.path.join(D, "inappfps.py")).read().split("ap = argparse")[0])
GAMES = {
    # (tap x, y), (swipe x0, y0, x1, y1) or seconds to wait
    "flightcontrol": [(.397, .127), 18, (.153, .629), 4],  # icon, PLAY! (Poco layout)
    "doodlejump": [(.143, .127), 22],                      # main menu (Pixel layout)
    "jellycar": [(.397, .127), 20, (.294, .497), 8],       # icon, dismiss the push prompt (Pixel layout)
    "jetpack": [(.857, .127), 50],                        # Jetpack Joyride title (Poco layout, after OpenFeint was declined)
    "bikeordie": [(.143, .310), 40],                      # Bike or Die!'s self-playing title map (Poco layout)
    # Asphalt 5, Aspen single race standing on the grid (auto-acceleration off in its options): icon, push
    # prompt OK, intro video: tap for its controls, Done (guest top right), title "touch to continue",
    # SINGLE RACE, NEXT (Mini Cooper), RACE, load, touch to start, camera fly-in. Poco layout. A tap that
    # lands a screen early ends on QUICK RACE's random track: check with FC_STEPS.
    "asphalt5": [(.857, .313), 10, (.294, .497), 12, (.5, .5), 1.5, (.92, .06), 20, (.5, .5), 8,
                 (.925, .916), 5, (.112, .523), 5, (.112, .523), 50, (.5, .5), 15],
    "ltbench-pixel": [(.9, .5, .15, .5), 3, (.857, .505), 30],  # LT Bench, Home page 3 (Pixel layout): one pass
    "ltbench": [(.143, .497), 40],                        # LT Bench, one pass (Poco layout)
    "doodlejump-poco": [(.143, .127), 22],                 # Doodle Jump main menu (Poco layout)
    "jellycar-poco": [(.619, .127), 20, (.294, .497), 8],  # JellyCar, dismiss the push prompt (Poco layout)
    "home": [2],                                           # stay on Home page 2 (FC_SHOT to see it)
}
apk = sys.argv[1]; secs = sys.argv[2] if len(sys.argv) > 2 else "30"
steps = GAMES[sys.argv[3] if len(sys.argv) > 3 else "jetpack"]
subprocess.run(["bash", os.path.join(D, "swapapk.sh"), apk], check=True)
if "FC_ENV" in os.environ:
    lines = "\\n".join(os.environ["FC_ENV"].split())
    subprocess.run(["adb", "shell", f"run-as org.lighttouch.android sh -c 'printf \"{lines}\\\\n\" > files/debug-env'"], check=True)
if os.environ.get("FC_FRAMES"):  # read by the engine at start; kept beside any other debug-env lines
    line = "LT_FRAME_LOG=/data/user/0/org.lighttouch.android/files/frames.log"
    subprocess.run(["adb", "shell", f"run-as org.lighttouch.android sh -c 'grep -q LT_FRAME_LOG files/debug-env 2>/dev/null || echo {line} >> files/debug-env'"], check=True)
subprocess.run([sys.executable, os.path.join(D, "inappfps.py"), "--boot-only"], check=True)
# One Power tap wakes the guest to the lock screen, which re-sleeps within ~5 s, so poll for it and
# swipe at once; tapping Power again before the wake shows (slow on the Poco) puts it back to sleep.
# Read the wake layout here, then refresh before taps: status/profile controls
# can reflow while the guest starts or changes orientation.
rect = guest_rect()
def unlocked():
    for _ in range(3):
        if sum(avg(rect, .1, .1, .9, .9)) < 15 or 'resource-id="guest_screen_asleep"' in ui():
            wake(); t = time.monotonic()
            while time.monotonic()-t < 8 and sum(avg(rect, .1, .1, .9, .9)) < 15: pass
        for _ in range(3):
            if not is_lock(rect): break
            sh("input", "swipe", *gpt(rect, .2, .89), *gpt(rect, .95, .89), "400"); time.sleep(1.5)
        if not is_lock(rect) and sum(avg(rect, .1, .1, .9, .9)) >= 15: return True
    return False
if not unlocked(): sys.exit("could not unlock the guest")
sh("input", "swipe", *gpt(rect, .9, .5), *gpt(rect, .15, .5), "300"); time.sleep(3)  # Home page 1 -> 2
for n, step in enumerate(steps):
    if isinstance(step, tuple):
        rect = guest_rect()
        if os.environ.get("FC_STEPS"):  # the screen each tap lands on, to check a long preset
            with open(os.path.join(os.environ["FC_STEPS"], f"step{n:02d}.png"), "wb") as f:
                f.write(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, check=True).stdout)
        if len(step) == 4: sh("input", "swipe", *gpt(rect, *step[:2]), *gpt(rect, *step[2:]), "300")
        else: sh("input", "tap", *gpt(rect, *step))
    else: time.sleep(step)
def save_shot():
    if os.environ.get("FC_SHOT"):
        with open(os.environ["FC_SHOT"], "wb") as f:
            f.write(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, check=True).stdout)
if float(secs) == 0:
    save_shot()
    sys.exit(0)
frames = None
if os.environ.get("FC_FRAMES"):  # matplotlib may live only in the system python
    py = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    frames = subprocess.Popen([py, os.path.join(D, "framestats.py"), "--device", secs, "--chart", os.environ["FC_FRAMES"],
                               "--label", os.path.basename(apk.rstrip("/").rsplit("/", 1)[0]),
                               "--save", os.environ["FC_FRAMES"] + ".frames.log"])
subprocess.run([sys.executable, os.path.join(D, "gamefps.py"), secs] + (["--series"] if os.environ.get("FC_SERIES") else []))
if frames: frames.wait()
save_shot()
