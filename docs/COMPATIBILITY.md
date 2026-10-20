# Compatibility runs

Which devices and iOS versions start on LightTouch, and how far they get. [`tools/compat/compat.py`](../tools/compat/compat.py)
boots a fresh device for one catalog firmware (`n81ap-10B500` and so on), presses Home once iOS has had
time to start, asks the guest tools which app is in front, shuts down, and records how far it got. It runs
the same way on headless Linux and on an Android phone; Linux is the fast loop, the phone confirms.

## Levels

Each level needs the ones before it.

| Level | Means |
| --- | --- |
| `admitted` | the engine accepted the prepared base and started |
| `lit` | the guest drew its first lit frame (iBoot or the kernel got as far as the display) |
| `tools` | LightTouch's guest tools loaded (`guest package … judged good` in the engine log) |
| `ui` | an app is in front: SpringBoard, or Setup on a base that was not set up (Android: the guest screen is lit and not just the Apple logo) |
| `clean` | iOS shut down cleanly when asked |

A firmware is **Tested** in the Android app (`testedFirmwares` in `FirmwareCatalog.kt`) once it reaches
`clean` on a phone.

## Running

```sh
python3 tools/compat/compat.py prepare n81ap-10B500   # on the Mac (firmwarekit), copied back to the bases folder
python3 tools/compat/compat.py linux n81ap-10B500 n72ap-7E18
ANDROID_SERIAL=... python3 tools/compat/compat.py android n81ap-10B500
python3 tools/compat/compat.py table                   # newest result per firmware and host
```

- Bases: `~/Documents/LightTouch-Development/compat/bases/<id>.itbase` (Light Touch for Mac's packed
  folder, or an `.itbase` archive); the iPod touch 2G also needs `bootrom.bin` there. `prepare` runs
  [`mac-prepare.sh`](../tools/compat/mac-prepare.sh) on the Mac: it downloads Light Touch for Mac's
  release and the IPSW (the catalog's link, then the Internet Archive), then `firmwarekit create` and
  `pack-base`.
- Linux needs `scripts/build.sh linux` and the guest tools in `build/guest-package/`. The base is imported
  once into `compat/work/<id>/base`; every run starts from an empty state.
- Android needs a debuggable APK (it reads the engine log with `run-as`) and no device running. A
  firmware with no device yet is added through **Add device › Choose a folder**, from the base pushed to
  the phone's Download folder.
- Results: one JSON per run in `compat/results/`, with milestone times, the screens it saw, and the log.

## Results

| Config | iOS | Host | Level | Lit (CPU s) | Tools up (s) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| n72ap-7E18 (iPod touch 2G) | 3.1.3 | Linux (Ryzen 7 3700X) | clean | 21.6 | 10.9 | SpringBoard |
| n72ap-7E18 (iPod touch 2G) | 3.1.3 | Poco F3 | clean | 24.8 | | lock screen; Start 4.5 s |
| n81ap-10B500 (iPod touch 4G) | 6.1.6 | Linux (Ryzen 7 3700X) | clean | 21.1 | 76.9 | SpringBoard; Setup on a fresh base |
| n81ap-10B500 (iPod touch 4G) | 6.1.6 | Poco F3 | clean | 6.1 | | lock screen after Setup; Start 4.6 s |

Known: the iPod touch 4G logs its first lit frame at 0.0 s wall time (CPU time is right).
