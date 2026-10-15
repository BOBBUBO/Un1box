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
relabelling a binary does not lower its glibc requirement. Host glibc, GCC runtime libraries and graphics
providers are deliberately excluded. Keeping the host's libstdc++/libgcc avoids
loading an older bundled C++ runtime into modern vendor graphics drivers; the
payload's required GLIBCXX/CXXABI symbols are recorded in runtime-manifest.json. Generic EGL/GLES/GL dispatch and vendor
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

The Debian 11 build recipe uses the signed archived Debian repositories and a
bounded Docker container. Keep its build product directory separate from a fresh
per-attempt evidence directory; the runner refuses to overwrite evidence. It
limits memory and memory+swap to 4 GiB, CPUs to two and PIDs to 256, samples the
actual cgroup, and stops at 25 minutes or below 6 GiB available host memory.
Admission requires 8 GiB available. All supplied source mounts stay read-only;
QEMU's editable Python setup writes only into a private source clone retaining
the exact commit. GCC 10 uses an explicitly stamped `gnu++17` override; upstream's
default is unchanged.

```sh
python3 Linux/packaging/run-bounded-build.py \
  --app /absolute/committed/LightTouch \
  --qemu /absolute/committed/qemu-ios \
  --output /private/build-products \
  --evidence /private/build-products/attempt-01 \
  --name lighttouch-bullseye-build01
```

The QEMU recipe must support `--cpp-standard=gnu++17`. Dependencies must first be
hash-verified and extracted by the pinned portable `--prepare-only` recipes using
a host Python with the safe tar extraction filter; Debian 11's Python 3.9 lacks
that API. Put these prepared public source trees in `OUTPUT/deps` and
`OUTPUT/crypto`. Never place private guest material in this build directory.
`collect-bullseye-runtime.py` records actual ELF dependency closure, also includes
SDL's dynamically loaded ALSA/Pulse roots, and bundles Python/Tcl/Tk, copyright
texts and native/dependency source archives. Its inventory records precise Debian
source versions. Distribution additionally requires retaining the complete
matching Debian/runtime source closure; an inventory alone is not that closure.
The AppDir still requires actual relocation, display/audio and guest tests.
