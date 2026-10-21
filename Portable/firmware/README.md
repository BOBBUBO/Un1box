# Firmware preparation

Every LightTouch port prepares devices with **firmwarekit** ([Packages/FirmwareKit](../../Packages/FirmwareKit), upstream Light Touch for Mac's preparer, ported to Linux and Android). It turns a stock IPSW into a prepared device for any board in [firmware-catalog.json](firmware-catalog.json), using only its own code: no mounts, no root, no macOS tools off the Mac.

```sh
scripts/build.sh firmwarekit          # build/firmwarekit/linux: firmwarekit, lighttouch-oneshot
scripts/build.sh firmwarekit-android  # build/firmwarekit/android (Swift SDK for Android, NDK r30)
scripts/build.sh firmware-tools       # on a Mac: build/firmware-tools (the guest tools preparation installs)

build/firmwarekit/linux/firmwarekit create --catalog Portable/firmware/firmware-catalog.json \
  --id n81ap-10B500 --ipsw iPod4,1_6.1.6_10B500_Restore.ipsw --out n81ap-10B500 \
  --helper build/firmwarekit/linux/lighttouch-oneshot --guest-tools build/firmware-tools
build/firmwarekit/linux/firmwarekit pack-base --base n81ap-10B500 --out n81ap-10B500.itbase
```

`create` prints one JSON event per line (`begin` with each step's expected seconds, `step`, `progress`, `warning`, `error`, `done`). Preparation boots the new system twice through `lighttouch-oneshot`, which loads the LightTouch engine (`LTM_QEMU_DYLIB`, else `libqemu-arm.so` beside it): once to create the data-protection keybag, and once so iOS finishes its first-start setup (`it_seal` halts it). Android runs the same two executables from the APK (`FirmwareService`).

Off the Mac, firmwarekit reads and writes disk images itself: UDIF to raw, then HFS+ extracted to a folder and rebuilt, case-insensitive volumes included. Hard links are kept as they were when unchanged, since Android cannot make them.

Measured: on Linux, n72ap-7E18 takes 35 s and n81ap-10B500 5½ minutes. On a Poco F3, n72ap-7E18 takes 54 s.

The other files here are historical audit notes from the earlier n72-only C++ preparer, and the read-only shutdown evidence tools ([SHUTDOWN-EVIDENCE.md](SHUTDOWN-EVIDENCE.md)).
