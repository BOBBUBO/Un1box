# x86_64 AppImage runtime packaging

`stage-appimage.py` consumes an explicit, hash-pinned runtime manifest. It never
fetches dependencies or includes firmware, guest tools, writable state or fixture
folders. Already prepared bases remain outside the AppImage and are referenced by
private launcher profiles. The same native frontend/shared engine used by other
Linux acceptance runs is the product payload; a different backend is not silently
substituted.

Build frontend, engine, dependencies and a relocatable Python 3.9+ with Tk 8.6 in
a glibc 2.31 x86_64 build environment. The compatibility target is kernel 5.4+,
glibc 2.31+ and X11/XWayland. Current-host artifacts must pass the symbol check;
relabelling a binary does not lower its glibc requirement. Host glibc and graphics
providers are deliberately excluded. Generic EGL/GLES/GL dispatch and vendor
GPU drivers must be supplied by the host; no software fallback is selected.

Manifest schema (each file has a real absolute source, destination, SHA-256 and
license provenance):

```json
{
  "schema": 1,
  "architecture": "x86_64",
  "glibc_max": "2.31",
  "python_stdlib": "python3.9",
  "files": [
    {
      "source": "/private/frozen/lighttouch-linux",
      "destination": "usr/bin/lighttouch-linux",
      "sha256": "ACTUAL_SHA256",
      "license": "GPL-2.0-or-later; see bundled notices and corresponding source"
    }
  ]
}
```

The complete list must also include `usr/lib/lighttouch/libqemu-arm.so`,
`usr/bin/python3`, `usr/lib/python3.9/` stdlib/extension modules including tkinter
and `_tkinter`, Tcl/Tk scripts under `usr/share/tcltk/{tcl8.6,tk8.6}`, required shared
libraries under `usr/lib/`, license texts and
`usr/share/licenses/LightTouch/SOURCE.txt` with the matching source/rebuild notice.
Resolve source symlinks before auditing. Do not use broad copies of `/usr`, HOME,
SDKs or private asset directories. Python's encoding modules and extension-module
runtime dependencies are required; the few mandatory-file checks are a minimum,
not a substitute for actual launched Python/Tk and guest tests. A manifest is not
an assertion of legal source completeness or runtime qualification.

```sh
/usr/bin/python3 Linux/packaging/stage-appimage.py \
  --manifest /private/audited-runtime-inputs.json \
  --output /private/package/LightTouch.AppDir
```

The stage verifies SHA-256 before and after copying, rejects private guest asset
names/path traversal/duplicate targets/host-specific RPATHs, checks every ELF is
x86_64 with GLIBC requirements at most 2.31, and checks declared ELF NEEDED closure.
It writes `runtime-manifest.json` with exact bundled hashes and host dependencies.
`dlopen` dependencies still require explicit runtime inventory and actual tests.

Use an independently pinned/audited `appimagetool` executable to seal the AppDir:

```sh
ARCH=x86_64 /private/tools/appimagetool /private/package/LightTouch.AppDir \
  /private/package/LightTouch-x86_64.AppImage
```

Record tool/version/hash, package hash, source revisions and complete payload in
private build receipts. AppImage sealing and binary builds require the shared
heavy-resource slot; do not run them concurrently with a guest. No AppImage or
broad Linux compatibility is claimed until the actual launcher, same-engine guest
boot/render/multi-touch/audio/clean restart and targeted distro matrix run.
AppImage FUSE is optional: users can use `--appimage-extract` and `squashfs-root/AppRun`
when FUSE is unavailable. The native guest worker semantics remain unchanged.
