# Get the iOS files

LightTouch needs three files for its main device, the **iPod touch 2G on iOS 3.1.3
(`n72ap-7E18`)**:

| File | What it is | LightTouch calls it |
| --- | --- | --- |
| `n72ap-7E18.itbase` | The prepared iOS system | **iOS system** (desktop) / **Import base** (Android) |
| `armv6.itpack` | Guest tools that run inside iOS | **Guest tools** |
| `bootrom.bin` | The n72 SecureROM boot file | **Boot file** (desktop) / **ROM** (Android) |

The upstream [LightTouchMac](https://github.com/samhenrigold/LightTouchMac/releases) app
release contains all three, and the routes below take them from it. Pick the first one
that suits you.

## 1. Easiest: let the script fetch them (Linux, Windows, macOS)

[`tools/get-assets.py`](../tools/get-assets.py) downloads the upstream app ZIP
(about 390 MB), checks its SHA-256 against the release it was tested with, and
unpacks the three files. It needs Python 3.8 or newer and nothing else; it does
not need a Mac.

From a checkout of this repository:

```sh
python3 tools/get-assets.py            # Windows: py tools\get-assets.py
```

Or without a checkout:

```sh
curl -LO https://raw.githubusercontent.com/blake-lucas/LightTouch/main/tools/get-assets.py
python3 get-assets.py
```

The files land in `LightTouch-assets/`. Then [load them](#4-load-the-files-in-lighttouch).

Options:

- `--zip LightTouchMac-universal.zip` uses a ZIP you already downloaded.
- `--out DIR` writes somewhere else.
- `--verified-tools` also writes `verified-tools/`, the folder that
  [IPSW preparation](#5-prepare-a-system-from-an-ipsw-instead) needs.
- `--armv7` also writes `armv7.itpack` for the iPod touch 4G route.
- A newer upstream release has a different checksum; the script refuses it unless you pass
  `--any-zip`.

**Android:** run the script on a computer, then copy the three files to the phone
(USB, cloud drive, or anywhere the Android file picker can see).

## 2. By hand from the upstream app ZIP

1. Download **`LightTouchMac-universal.zip`** from the
   [upstream release](https://github.com/samhenrigold/LightTouchMac/releases/tag/v1.0-2026.10.15b).
2. Open it with any ZIP tool (Windows Explorer, `unzip`, 7-Zip, Archive Utility) and
   copy two files out of `Light Touch.app/Contents/Resources/Device/`:
   - `n72ap-7E18.itbase`
   - `bootrom_240_4`: rename your copy to **`bootrom.bin`**.
3. The guest tools are inside `Light Touch.app/Contents/Resources/Guest/guest.aar`, an
   Apple Archive. Ordinary ZIP tools cannot open it:
   - **macOS:** `aa extract -i guest.aar -d guest`, then copy `guest/guest-tools/armv6.itpack`.
   - **Linux / Windows:** `python3 tools/get-assets.py --zip LightTouchMac-universal.zip`
     unpacks it (this is route 1 without the download).

## 3. If you already have the files

Keep the `.itbase` as it is: do not unzip it, and do not rename an IPSW to `.itbase`.
The board, iOS build and guest architecture must match; for the iPod touch 4G route see
[route 5](#5-prepare-a-system-from-an-ipsw-instead).

## 4. Load the files in LightTouch

**Desktop (Linux, Windows, macOS):** in **iOS files**, choose `n72ap-7E18.itbase` in
**1 iOS system**, `armv6.itpack` in **2 Guest tools** and `bootrom.bin` in **3 Boot file**
(companion files in the same folder may fill in by themselves). Click **Set up device**,
wait for the import to finish, then **Start iOS**. Use **Stop** and wait for shutdown
before closing.

**Android:** tap **Import base** and choose `n72ap-7E18.itbase`, then **ROM** for
`bootrom.bin` and **Guest tools** for `armv6.itpack`. Then **Start**.

The app checks the files itself; you never type hashes. Each saved device has its own
writable storage, and the imported system stays read-only, so one import can back several
devices (**New from selected** on desktop).

## 5. Prepare a system from an IPSW instead

Preparation turns Apple's stock firmware into an emulator-ready system. Importing a stock
IPSW in the iOS system picker does **not** do this. Every route needs the upstream guest
tools folder: make it with `python3 tools/get-assets.py --verified-tools`.

| Host | n72ap-7E18 (iPod touch 2G, iOS 3.1.3) | n81ap-8C148 (iPod touch 4G, iOS 4.2.1) |
| --- | --- | --- |
| Android | In-app: **Prepare from stock IPSW** | Upstream Mac app only |
| Linux | `lighttouch-prepare` (build it from [Portable/firmware](../Portable/firmware/README.md)) | Upstream Mac app only |
| macOS | Upstream app's **Add Device**, or its bundled `firmwarekit` | Same |
| Windows | Use one of the others, then copy the `.itbase` | Same |

The stock n72 input is `iPod2,1_3.1.3_7E18_Restore.ipsw` (SHA-1 and size are checked
against the [firmware catalog](../LightTouchMac/Resources/firmware-catalog.json)). Apple
no longer hosts this update; the catalog lists archive mirrors.

**Android:** copy the IPSW and the `verified-tools` folder to the phone. Tap **Prepare
from stock IPSW**, choose `n72ap-7E18`, then **Stock IPSW** and **Verified tools** (pick the
`verified-tools` folder), and **Prepare**. The prepared system is imported when it finishes.

**Linux:**

```sh
python3 tools/get-assets.py --verified-tools
lighttouch-prepare LightTouchMac/Resources/firmware-catalog.json n72ap-7E18 \
  iPod2,1_3.1.3_7E18_Restore.ipsw \
  LightTouch-assets/verified-tools/release-guest-unpacked/guest-tools \
  LightTouch-assets/verified-tools/inventory.json \
  n72ap-7E18-prepared
```

The output folder must not exist yet. Load it with **Import prepared folder instead** (Android) or
pack it with the upstream `firmwarekit pack-base` to get an `.itbase`.

**macOS, and the n81 route:** in the upstream app use **Add Device** for the board and
build (n81 is marked experimental upstream), or run its bundled tool directly:

```sh
APP="/Applications/Light Touch.app"
"$APP/Contents/MacOS/firmwarekit" create --catalog /path/to/firmware-catalog.json \
  --id n81ap-8C148 --ipsw /path/to/iPod4,1_4.2.1_8C148_Restore.ipsw --out n81ap-8C148-prepared
"$APP/Contents/MacOS/firmwarekit" pack-base --base n81ap-8C148-prepared --out n81ap-8C148.itbase
```

n81 uses `armv7.itpack` (`get-assets.py --armv7`) and **no** boot file: its prepared system
boots from its own `kboot.bin`. See upstream's
[preparation notes](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/CONTRIBUTING.md#firmwares-in-the-catalog).

## File types

| File | Purpose |
| --- | --- |
| `.ipsw` | Apple's stock firmware; input to preparation only. |
| `.itbase` | A prepared, read-only system archive (includes `device.lock.json`). |
| `armv6.itpack` / `armv7.itpack` | Guest tools for the guest CPU. Preparation outputs may name the matched one `guest.itpack`. |
| `bootrom.bin` / `bootrom_240_4` | The n72 SecureROM. Not the same as the `iBoot.bin` inside a prepared system. |
| `kboot.bin` | The n81 boot file, already inside its prepared system. |
| `guest.aar` | Upstream's Apple Archive of guest tools, developer disk images and helpers. |

Storage note: the desktop importer stores n72 NAND as one indexed file. Alpha builds do not
migrate old library layouts; keep the original `.itbase` so you can re-import.

Use firmware you are entitled to use, and keep firmware, ROMs and personal guest data out
of public bug reports and repositories.
