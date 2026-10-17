# Open work

Tick items off here as they land (with the commit), so nothing is lost between sessions.
Performance results go in [PERFORMANCE.md](PERFORMANCE.md).

## Performance (current focus)

- [ ] Android: steady ~60 Hz guest UI (Springboard animations measure 45–51 fps on Pixel 10 / Poco F3).
- [ ] Android: steady target frame rate in common period games (Flight Control ~24 fps on Poco F3).
  Profile the GL passthrough / readback present path during play (`tools/perf/gameprof.sh`).
- [ ] Try the engine's upstream merge (`qemu-ios-android` `dev/upstream-sync`, 7ab545390b) on a phone;
  verified on Linux only.
- [ ] Desktop frontends poll for frames (Linux 10 ms loop, Windows `SDL_Delay(8)`); move them to
  frame-published wakeups like Android.
- [x] Document every performance change and its measurement: [PERFORMANCE.md](PERFORMANCE.md).

## Firmware and assets

- [x] Rewrite [ASSETS.md](ASSETS.md) easiest route first: `tools/get-assets.py` (downloads the
  upstream app ZIP and unpacks it on any OS, including guest.aar's LZFSE Apple Archive), then by
  hand, then IPSW preparation. `--verified-tools` makes the folder IPSW preparation needs
  (Linux `lighttouch-prepare` verified with it).
- [ ] Owner decision: attach `LightTouch-n72ap-7E18.zip` (itbase + armv6.itpack + bootrom.bin,
  built locally in `~/Documents/LightTouch-Development/release-assets/`) to a release. It is
  Apple's copyrighted firmware; upstream and devos50/qemu-ios `n72ap_v1` host the same kind of
  files, but rehosting carries DMCA-takedown risk. If attached, add it as route 1 in ASSETS.md.
- [ ] Android: download and unpack the upstream files in-app (port the get-assets.py decoder), so
  phone-only users need no computer and nothing is rehosted.

## Cleanup

- [ ] Prune code no port uses: the upstream LightTouchMac app/Xcode project and Swift packages
  (our macOS port is `macOS/` + `Desktop/`), and anything else only they reference. Keep
  `LightTouchMac/Resources/firmware-catalog.json` (Android assets, `lighttouch-prepare` and
  ASSETS.md use it): move it and update those references.

## Android bugs seen, not yet fixed

- [ ] The first touch after the guest wakes is often ignored (unlock needs a retry). Partly the
  "Guest screen asleep" label: it sits above the guest view and disappears on wake, so the view
  jumps down under the finger. Reserve its space or overlay it.
- [ ] Stop was seen disabled once after Store use; not reproduced.
- [ ] Store overlay lacks `testTagsAsResourceId`, so UI automation cannot find its controls.
- [ ] Some catalog apps marked compatible need iOS 4 APIs (Doodle Jump 3.x: GCD). Consider flagging
  apps that weak-link iOS 4 symbols, or surfacing Last crash automatically after a quick quit.
