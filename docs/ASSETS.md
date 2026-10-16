# Get the iOS system, guest tools and boot file

Start with **iPod touch 2G / iOS 3.1.3 (`n72ap-7E18`)**. It is this fork's
main exercised baseline. The prepared **iPod touch 4G / iOS 4.2.1
(`n81ap-8C148`)** route is also implemented, with further qualification ongoing.
Upstream's larger device/version list is not this fork's compatibility list.

## 1. If you already have the files

1. Extract any outer download ZIP into a normal folder. Keep the `.itbase`
   intact; do not unzip it or rename an IPSW to `.itbase`.
2. Put the matching files together:

   | System | iOS system | Guest tools | Separate boot file |
   | --- | --- | --- | --- |
   | iPod touch 2G, iOS 3.1.3 | `n72ap-7E18.itbase` | `armv6.itpack` | `bootrom.bin`, containing the matching `bootrom_240_4` SecureROM |
   | iPod touch 4G, iOS 4.2.1 | `n81ap-8C148.itbase` | `armv7.itpack` | None: the prepared system includes `kboot.bin` |

3. Open LightTouch. In **iOS files**, choose the `.itbase` in **1 iOS system**,
   the matching `.itpack` in **2 Guest tools**, and the n72 ROM in **3 Boot file**.
   For n81, leave **Boot file** empty. Nearby companion files may fill in
   automatically; check the selections.
4. Click **Set up device** and wait for import and verification to finish.
   The app calculates hashes itself; you do not need to type or paste hashes.
5. Click **Start iOS**. Use **Stop** and wait for confirmed shutdown before
   closing. Each saved device has independent writable storage; the imported
   system stays immutable.

Use files from the same matched preparation/export, suitable for your port build.
The board, OS build and guest architecture must agree. A filename alone does not
prove that a newer upstream guest package matches an older emulator build.
**New from selected** creates fresh saved data using an imported system.

## 2. Get the baseline files from the public upstream Mac release

The public [LightTouchMac releases](https://github.com/samhenrigold/LightTouchMac/releases)
provide the upstream Mac app. Its documented bundle includes the prepared n72
system, boot assets and guest-tools archive. This is a source of assets, not an
installation of this fork's Linux/Windows/Android/macOS ports.

1. Open the release page and download **`LightTouchMac-universal.zip`**. Unzip it
   to obtain **`Light Touch.app`**. The release checked for this guide was
   [v1.0-2026.10.15b](https://github.com/samhenrigold/LightTouchMac/releases/tag/v1.0-2026.10.15b).
   That release lists the app ZIP and `SHA256SUMS`; it does not list a standalone
   `.itbase` or guest `.itpack` download.
2. On a Mac, choose **Show Package Contents** on `Light Touch.app`. Copy these
   files out of `Contents/Resources/Device/` into your own assets folder:
   - `n72ap-7E18.itbase`
   - `bootrom_240_4` — name your copy **`bootrom.bin`** for this fork's picker.
3. Extract `Contents/Resources/Guest/guest.aar` using macOS AppleArchive. For an
   app installed in Applications, these Terminal commands create a separate
   assets folder and leave the app bundle unchanged:

   ```sh
   APP="/Applications/Light Touch.app"
   ASSETS="$HOME/LightTouch-assets"
   mkdir -p "$ASSETS/guest"
   cp "$APP/Contents/Resources/Device/n72ap-7E18.itbase" "$ASSETS/"
   cp "$APP/Contents/Resources/Device/bootrom_240_4" "$ASSETS/bootrom.bin"
   /usr/bin/aa extract -i "$APP/Contents/Resources/Guest/guest.aar" -d "$ASSETS/guest"
   cp "$ASSETS/guest/guest-tools/armv6.itpack" "$ASSETS/"
   ```

4. Keep those three files together and follow [step 1](#1-if-you-already-have-the-files).
   To use them on another host, transfer the copied files, not the Mac app.

The app bundle's `.itbase` and ROM can also be copied from its ZIP on another
host. The `aa` command above is a **Mac** tool: on Windows or Linux, obtain a
matching exported `armv6.itpack` or extract `guest.aar` on a Mac first. The source
for the bundle layout is upstream's
[packaging code](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/Packages/BuildTools/Sources/BuildTools/Vendor.swift)
and [guest archive reader](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/Packages/FirmwareKit/Sources/FirmwareSchema/GuestArchive.swift).

## 3. Prepare another supported system from an IPSW

Use this workflow when you need a new prepared base, such as n81. Preparation
changes stock firmware into an emulator-ready system; importing a stock IPSW
in the desktop `.itbase` picker does not perform that conversion.

1. On a Mac, use the upstream app's **Add Device** workflow for the matching
   board/build, or supply its exact stock IPSW. Upstream downloads and checks
   stock inputs according to its
   [firmware catalog](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/LightTouchMac/Resources/firmware-catalog.json).
   For n81, select **iPod touch 4G, iOS 4.2.1, build 8C148**; that entry is
   marked experimental. It names an Apple-hosted IPSW and an archive mirror.
   The n72 3.1.3 entry uses third-party archives, as Apple did not host that update.
2. For a reproducible standalone output, use the upstream app's bundled
   `Contents/MacOS/firmwarekit`. With the matching upstream source checkout
   providing the catalog, the n81 commands are:

   ```sh
   APP="/Applications/Light Touch.app"
   CATALOG="/path/to/LightTouchMac/LightTouchMac/Resources/firmware-catalog.json"
   ASSETS="$HOME/LightTouch-assets"
   mkdir -p "$ASSETS"
   "$APP/Contents/MacOS/firmwarekit" create \
     --catalog "$CATALOG" --id n81ap-8C148 \
     --ipsw "/path/to/iPod4,1_4.2.1_8C148_Restore.ipsw" \
     --out "$ASSETS/n81ap-8C148-prepared"
   "$APP/Contents/MacOS/firmwarekit" pack-base \
     --base "$ASSETS/n81ap-8C148-prepared" \
     --out "$ASSETS/n81ap-8C148.itbase"
   ```

   Replace the two `/path/to/` examples with your actual files. Use a new output
   folder, allow room for the expanded system, and run `pack-base` only after
   preparation reports success. The bundled tool uses its sibling helper and
   bundled guest tools by default; preparation can run helper guest boots.
3. Extract the same app's `guest.aar` as above and use
   **`guest-tools/armv7.itpack`** for n81. Keep the generated `.itbase`, matching
   guest package and preparation identity together.
4. Import the `.itbase` using [step 1](#1-if-you-already-have-the-files). Keep n81's
   **Boot file** empty: its prepared `kboot.bin`, NAND and NOR provide that route.
   Do not substitute an n72 ROM or ARMv6 guest package.

See the upstream [preparation instructions](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/CONTRIBUTING.md#firmwares-in-the-catalog)
and [CLI definitions](https://github.com/samhenrigold/LightTouchMac/blob/v1.0-2026.10.15b/Packages/FirmwareKit/Sources/FirmwareSchema/FirmwareCommand.swift)
for other options. This fork also has a developer-oriented
[native n72 preparation route](../Portable/firmware/README.md); it requires verified
stock firmware and guest-tool inputs and does not imply native preparation of
all upstream devices on every host. Upstream guest-tool build/export source is
in [qemu-ios](https://github.com/samhenrigold/qemu-ios/tree/main/contrib), with
build inputs documented in upstream's contributor guide.

## What the file extensions mean

| File | Purpose |
| --- | --- |
| `.zip` | Outer download container. Extract it before choosing individual assets. |
| `.ipsw` | Stock Apple firmware used by a preparation tool, before emulator-specific preparation. |
| `.itbase` | Prepared immutable system archive, including `device.lock.json` and system assets. |
| `armv6.itpack` / `armv7.itpack` | Guest software package for the appropriate guest CPU architecture; not a host installer or system archive. Some preparation outputs name their matched package `guest.itpack`. |
| `bootrom.bin` / `bootrom_240_4` | n72 SecureROM boot asset. `iBoot.bin` inside a prepared base is a different file. |
| `kboot.bin` | n81 prepared kernel boot asset, already inside its base. |

The new common desktop importer streams n72 NAND into a single indexed file
instead of creating one host file per page. n81 retains its supported directory
layout with large NAND files. This is an alpha storage change: old library
layouts have no migration guarantee; re-import the original `.itbase` when
required. Keep the original archive and saved data until the new setup works.

Use firmware and boot assets from sources you are entitled to use. Keep private
firmware, ROMs and personal guest data out of public bug reports and source
repositories. This guide links public upstream software and preparation
instructions; catalog presence is not proof of port compatibility.
