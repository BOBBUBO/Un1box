#!/usr/bin/env python3
"""Desktop: run a headless guest and capture its syslog (the guest's syslog_relay service) through the
app's own USB bridge, then count the messages by sender and text.

A guest app writing to stderr costs more than the write: launchd relays every write to syslogd as a
message of its own, and syslogd parses each one. That is how the GL shim's per-frame lines were found
(engine 7f744e80cc). Run it with the AppImage's own Python, which has app_services:

  APPDIR=.../squashfs-root $APPDIR/usr/bin/python3 -s tools/perf/guestsyslog.py \\
      --library LIB --record ID --engine libqemu-arm.so --frontend lighttouch-linux \\
      --script fc.txt --seconds 160 --start 70 --out syslog.txt [--package guest.itpack]

The frontend's own output goes to OUT.log. --start is when to connect (the guest must be up).
"""
import argparse, collections, ctypes as C, os, re, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
for a in ("library", "record", "engine", "frontend", "script", "out"):
    ap.add_argument("--" + a, required=True)
ap.add_argument("--seconds", type=int, default=160)
ap.add_argument("--start", type=int, default=70)
ap.add_argument("--package")
args = ap.parse_args()

A = Path(os.environ["APPDIR"])
sys.path.insert(0, str(A / "usr/share/lighttouch"))
from app_services.bridge import Bridge
from app_services.mobile import Mobile

out = Path(args.out).resolve()
data = out.parent / (out.name + ".bridge")
(data / "pairing").mkdir(parents=True, exist_ok=True); (data / "logs").mkdir(exist_ok=True)
lib, plist = A / "usr/lib/lighttouch/libimobiledevice-1.0.so.6", A / "usr/lib/lighttouch/libplist-2.0.so.4"
bridge = Bridge(A / "usr/bin/usbmuxd-qemu", lib, plist, data / "pairing", data / "logs")
env = dict(bridge.start())
env.update({k: v for k, v in os.environ.items() if k.startswith(("LT_", "IT_"))})
cmd = [args.frontend, "--library", args.library, "--record", args.record, "--engine", args.engine,
       "--headless", "--seconds", str(args.seconds), "--no-audio", "--script", args.script]
if args.package:
    cmd += ["--package", args.package]
guest = subprocess.Popen(cmd, env=env, stdout=open(str(out) + ".log", "wb"), stderr=subprocess.STDOUT)
text = bytearray()
try:
    time.sleep(args.start)
    os.environ["USBMUXD_SOCKET_ADDRESS"] = "UNIX:" + str(bridge.socket)
    os.chdir(str(A / "usr/share/lighttouch"))
    m = Mobile(lib, plist)
    m.lib.syslog_relay_receive_with_timeout.argtypes = [C.c_void_p, C.c_char_p, C.c_uint32,
                                                        C.POINTER(C.c_uint32), C.c_uint]
    client = m.service("com.apple.syslog_relay", "syslog_relay")
    buf, got, t0 = C.create_string_buffer(65536), C.c_uint32(), time.monotonic()
    while guest.poll() is None:
        got.value = 0
        m.lib.syslog_relay_receive_with_timeout(client, buf, len(buf), C.byref(got), 500)
        text += buf.raw[:got.value]
    secs = time.monotonic() - t0
    guest.wait(timeout=60)
finally:
    if guest.poll() is None:
        guest.terminate()
    bridge.close()
out.write_bytes(bytes(text))
lines = [l for l in text.decode("utf-8", "replace").replace("\x00", "").splitlines() if l.strip()]
print(f"{len(lines)} lines in {secs:.0f} s ({len(lines) / secs:.1f}/s); most frequent, digits folded:")
key = lambda l: re.sub(r"\d+", "N", re.sub(r"^\s*\w{3} \w{3}\s+\d+ [\d:]+ \S+ ", "", l))[:110]
for line, n in collections.Counter(map(key, lines)).most_common(12):
    print(f"{n:7} {line}")
