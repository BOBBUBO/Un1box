#!/bin/bash
# Build, package and publish a LightTouch release for every platform. Each step works in a release
# workspace ($LT_RELEASE_ROOT/release-X.Y.Z) from worktrees of the release tags, so the checkouts you
# work in are never built from. See docs/BUILDING.md, "Release builds".
#
#   scripts/build-release.sh VERSION [STEP...]        e.g. scripts/build-release.sh 0.0.8
#
#   prepare   worktrees app/ and engine/ at tags vX.Y.Z (here) and lighttouch-vX.Y.Z (qemu-ios)
#   android   non-debuggable arm64 APK carrying the guest tools ($LT_GUEST_TOOLS)
#   windows   engine + FFmpeg cross-built with MinGW, then the worker, runtime and zip
#   linux     engine, frontend and tests in the Debian 11 image (Docker), then the AppImage
#   macos     engine and app on the Mac ($LT_MAC) over ssh, from the pushed commits
#   finish    source archives, guest tools, SHA256SUMS, RELEASE-MANIFEST.json, NOTES.md checksums
#   publish   push both tags and create the GitHub release from out/NOTES.md (a pre-release unless
#             LT_PRERELEASE=false)
#
# With no STEP it runs prepare through finish, skipping the steps already done (a .done-STEP file in
# the workspace); naming a step runs it again. publish only runs when named. Write out/NOTES.md
# (title on its first line, CHECKSUMS where the checksum block goes) before finish.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/scripts/build.env" ] && . "$REPO/scripts/build.env"

D=$HOME/Documents/LightTouch-Development
: "${QEMU_IOS:=$HOME/git-projects/qemu-ios}"                         # engine repository (tags, subprojects)
: "${LT_INPUTS:=$D/port-workspace/builds}"                           # prebuilt inputs, as for scripts/build.sh
: "${LT_RELEASE_ROOT:=$D}"                                           # workspaces go in release-X.Y.Z here
: "${LT_GUEST_TOOLS:=$D/guest-tools/serial-31}"                      # armv6/armv7.itpack shipped and bundled
: "${LT_LINUX_INPUTS:=$D/release-0.0.8/linux-build}"                 # deps-full/ crypto/ mobile-private-runtime-ui15/
: "${LT_LINUX_IMAGE:=lighttouch-bullseye-collector08}"               # Debian 11 build image
: "${LT_APPIMAGE_TOOLS:=$LT_INPUTS/linux-desktop/dependencies/appimagetool-pinned01}"
: "${LT_WIN_DEPS:=$D/release-0.0.8/win-deps}"                        # MinGW sysroot (copied; FFmpeg installs into it)
: "${LT_WIN_INPUTS:=$LT_INPUTS/windows-native}"                      # python-tk01/, services-mobile01/, FFmpeg archive
: "${LT_WIN_HELPER:=$D/release-0.0.8/wbuild/helper/publish}"         # LightTouchWin helper (dotnet publish; absent: publish it first)
: "${LT_FFMPEG_ARCHIVE:=$LT_WIN_INPUTS/package02/source-and-provenance/ffmpeg-9.0.1.tar.xz}"
: "${LT_MAC:=}"                                                      # user@host of the Mac
: "${LT_MAC_ROOT:=Documents}"                                        # on the Mac, relative to home
: "${LT_MAC_PYTHON:=/opt/homebrew/bin/python3.13}"
: "${LT_MAC_OPENSSL:=/opt/homebrew/opt/openssl@3}"
: "${LT_MAC_SDL3:=/opt/homebrew/Cellar/sdl3/3.4.18/lib/libSDL3.0.dylib}"
: "${LT_MAC_VENDOR:=/Applications/Light Touch.app/Contents}"         # LightTouchMac app: firmwarekit, vendor files
: "${LT_MAC_PYTHON_RUNTIME:=\$HOME/Documents/LightTouch-Python-ActualInterpreter12/runtime}"
: "${LT_PRERELEASE:=true}"
: "${ANDROID_HOME:=$HOME/Android/Sdk}"
export QEMU_IOS LT_INPUTS ANDROID_HOME

[ $# -ge 1 ] || { sed -n 2,21p "$0"; exit 2; }
case "$1" in -h|--help) sed -n 2,21p "$0"; exit 0 ;; esac
VER=${1#v}; V=v$VER; shift
R=$LT_RELEASE_ROOT/release-$VER
OUTD=$R/out
GH_APP=https://github.com/blake-lucas/LightTouch.git
GH_ENGINE=https://github.com/blake-lucas/qemu-ios.git
# Subprojects meson would download; copied in from $QEMU_IOS (git ignores them).
SUBPROJECTS="berkeley-softfloat-3 berkeley-testfloat-3 keycodemapdb dtc slirp"
log() { printf '== %s: %s\n' "$V" "$*"; }
need() { [ -e "$1" ] || { echo "missing $2: $1" >&2; exit 1; }; }
rev() { git -C "$1" rev-parse HEAD; }

step_prepare() {
    local app_rev engine_rev
    app_rev=$(git -C "$REPO" rev-parse --verify -q "$V^{commit}") || { echo "tag $V first (git tag $V)" >&2; exit 1; }
    engine_rev=$(git -C "$QEMU_IOS" rev-parse --verify -q "lighttouch-$V^{commit}") ||
        { echo "tag lighttouch-$V in $QEMU_IOS first" >&2; exit 1; }
    # The Mac fetches the commits from GitHub.
    [ -n "$(git -C "$REPO" branch -r --contains "$app_rev")" ] || { echo "push $V's commit first" >&2; exit 1; }
    [ -n "$(git -C "$QEMU_IOS" branch -r --contains "$engine_rev")" ] || { echo "push lighttouch-$V's commit first" >&2; exit 1; }
    mkdir -p "$OUTD"
    [ -e "$R/app" ] || git -C "$REPO" worktree add -q --detach "$R/app" "$app_rev"
    [ -e "$R/engine" ] || git -C "$QEMU_IOS" worktree add -q --detach "$R/engine" "$engine_rev"
    [ "$(rev "$R/app")" = "$app_rev" ] && [ "$(rev "$R/engine")" = "$engine_rev" ] ||
        { echo "$R/app or $R/engine is not at the tags; remove the worktree and rerun" >&2; exit 1; }
    for p in $SUBPROJECTS; do
        need "$QEMU_IOS/subprojects/$p" "subproject $p (configure the engine once in $QEMU_IOS)"
        rm -rf "$R/engine/subprojects/$p"; cp -a "$QEMU_IOS/subprojects/$p" "$R/engine/subprojects/"
    done
    for w in app engine; do
        [ -z "$(git -C "$R/$w" status --porcelain --untracked-files=all)" ] || { echo "$R/$w is not clean" >&2; exit 1; }
    done
    echo "app $app_rev, engine $engine_rev"
}

step_android() {
    need "$LT_GUEST_TOOLS/armv6.itpack" "guest tools"; need "$LT_GUEST_TOOLS/armv7.itpack" "guest tools"
    QEMU_IOS="$R/engine" LT_GUEST_TOOLS="$LT_GUEST_TOOLS" LT_DEBUGGABLE=false \
        LT_SOURCE_REFERENCE="https://github.com/blake-lucas/LightTouch/tree/$V" \
        bash "$R/app/scripts/build.sh" --clean android
    local apk=$OUTD/LightTouch-$V-android-arm64.apk aapt
    cp "$R/app/build/android/LightTouch-debug.apk" "$apk"
    aapt=$(ls -d "$ANDROID_HOME"/build-tools/*/aapt | tail -1)
    if "$aapt" dump xmltree "$apk" AndroidManifest.xml | grep -q 'android:debuggable.*0xffffffff'; then
        echo "$apk is debuggable" >&2; exit 1
    fi
    "$aapt" dump badging "$apk" | grep -E "^package:" | grep -q "versionName='$VER'" ||
        { echo "$apk is not versionName $VER" >&2; exit 1; }
    echo "$apk"
}

step_windows() {
    need "$LT_WIN_DEPS/sysroot" "Windows dependency sysroot"; need "$LT_WIN_HELPER" "Windows helper"
    need "$LT_FFMPEG_ARCHIVE" "FFmpeg 9.0.1 archive"
    rm -rf "$R/win-cross" "$R/win-deps" "$R/wbuild" "$R/evidence-win"
    cp -a "$LT_WIN_DEPS" "$R/win-deps"
    bash "$R/app/Windows/tools/build-engine.sh" "$R/engine" "$R/win-deps" "$R/win-cross" "$LT_FFMPEG_ARCHIVE" \
        > "$R/windows-engine.log" 2>&1 || { tail -30 "$R/windows-engine.log" >&2; exit 1; }
    python3 "$R/app/scripts/release/package-windows.py" "$R" "$V" "$LT_WIN_INPUTS" "$LT_WIN_HELPER" \
        > "$R/windows-package.log" 2>&1 || { tail -30 "$R/windows-package.log" >&2; exit 1; }
    cp "$R/wbuild/package/LightTouch-$V-Windows-x86_64.zip" "$OUTD/"
    echo "$OUTD/LightTouch-$V-Windows-x86_64.zip"
}

step_linux() {
    local b=$R/linux-build mounts=() d p
    # The prebuilt prefixes go where they were built: their pkg-config files name /build/<dir>.
    for p in deps-full crypto mobile-private-runtime-ui15; do
        need "$LT_LINUX_INPUTS/$p" "Linux input $p"
        mounts+=(--mount "type=bind,src=$LT_LINUX_INPUTS/$p,dst=/build/$p,readonly")
    done
    # The worktrees' .git files point into these repositories (and their object alternates).
    for d in "$(git -C "$R/app" rev-parse --path-format=absolute --git-common-dir)" \
             "$(git -C "$R/engine" rev-parse --path-format=absolute --git-common-dir)"; do
        mounts+=(--mount "type=bind,src=$d,dst=$d,readonly")
        if [ -f "$d/objects/info/alternates" ]; then
            while read -r a; do mounts+=(--mount "type=bind,src=$a,dst=$a,readonly"); done < "$d/objects/info/alternates"
        fi
    done
    # Earlier runs leave root-owned files.
    [ -d "$b" ] && docker run --rm -v "$b:/b" "$LT_LINUX_IMAGE" sh -c 'rm -rf /b/* /b/.[!.]*'
    mkdir -p "$b"
    docker rm -f "lt-$V-linux" > /dev/null 2>&1 || true
    timeout 5400 docker run --name "lt-$V-linux" --rm --network none --memory=8g --memory-swap=8g --cpus=4 --pids-limit=256 \
        --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --security-opt no-new-privileges \
        --mount "type=bind,src=$R/app,dst=/src/app,readonly" \
        --mount "type=bind,src=$R/engine,dst=$R/engine,readonly" \
        --mount "type=bind,src=$b,dst=/build" "${mounts[@]}" \
        -e "QEMU_SOURCE=$R/engine" -e "EXPECTED_QEMU_REV=$(rev "$R/engine")" -e "EXPECTED_APP_REV=$(rev "$R/app")" \
        "$LT_LINUX_IMAGE" /bin/sh /src/app/scripts/release/linux-inner.sh > "$R/linux.log" 2>&1 ||
        { tail -30 "$R/linux.log" >&2; exit 1; }
    rm -rf "$R/evidence-seal"
    python3 "$R/app/Linux/packaging/seal-appimage.py" --appdir "$b/runtime/LightTouch.AppDir" \
        --tool "$LT_APPIMAGE_TOOLS/appimagetool-x86_64.AppImage" --runtime "$LT_APPIMAGE_TOOLS/runtime-x86_64" \
        --output "$OUTD/LightTouch-$V-Linux-x86_64.AppImage" --evidence "$R/evidence-seal" > "$R/linux-seal.log" 2>&1 ||
        { tail -30 "$R/linux-seal.log" >&2; exit 1; }
    echo "$OUTD/LightTouch-$V-Linux-x86_64.AppImage"
}

step_macos() {
    [ -n "$LT_MAC" ] || { echo "the macOS build runs on a Mac: set LT_MAC=user@host" >&2; exit 1; }
    local m="$LT_MAC_ROOT/LightTouch-Release-$VER" app_rev engine_rev zip=LightTouch-$V-macOS-arm64.zip
    app_rev=$(rev "$R/app"); engine_rev=$(rev "$R/engine")
    ssh "$LT_MAC" "set -e; rm -rf $m; mkdir -p $m; cd $m
        git init -q app; git -C app fetch -q --depth 1 $GH_APP $app_rev; git -C app checkout -q --detach FETCH_HEAD
        git init -q qemu; git -C qemu fetch -q --depth 1 $GH_ENGINE $engine_rev; git -C qemu checkout -q --detach FETCH_HEAD"
    for p in $SUBPROJECTS; do rsync -a "$R/engine/subprojects/$p" "$LT_MAC:$m/qemu/subprojects/"; done
    ssh "$LT_MAC" "export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin; cd $m
        $LT_MAC_PYTHON app/macOS/build-engine.py --qemu-source \$PWD/qemu --output \$PWD/engine --python $LT_MAC_PYTHON \
            --openssl-prefix $LT_MAC_OPENSSL --jobs 2 > engine.log 2>&1 || { tail -30 engine.log; exit 1; }
        /usr/bin/python3 -B app/macOS/build-shared.py --output \"\$PWD/LightTouch Shared Desktop.app\" --scratch \$PWD/scratch \
            --engine \$PWD/engine/libqemu-arm.dylib --engine-revision $engine_rev \
            --firmwarekit '$LT_MAC_VENDOR/MacOS/firmwarekit' --python-runtime $LT_MAC_PYTHON_RUNTIME \
            --vendor-contents '$LT_MAC_VENDOR' --sdl3-runtime $LT_MAC_SDL3 > shared.log 2>&1 || { tail -30 shared.log; exit 1; }
        codesign --verify --deep --strict 'LightTouch Shared Desktop.app'
        ditto -c -k --keepParent 'LightTouch Shared Desktop.app' $zip"
    scp -q "$LT_MAC:$m/$zip" "$LT_MAC:$m/LightTouch Shared Desktop.build-receipt.json" "$OUTD/"
    mv "$OUTD/LightTouch Shared Desktop.build-receipt.json" "$R/macOS-build-receipt.json"
    echo "$OUTD/$zip"
}

step_finish() {
    python3 "$R/app/scripts/release/finish.py" "$R" "$V" "$LT_GUEST_TOOLS"
    (cd "$OUTD" && sha256sum -c --quiet SHA256SUMS)
    if [ ! -f "$OUTD/NOTES.md" ]; then echo "next: write $OUTD/NOTES.md (with CHECKSUMS), then rerun finish"
    elif grep -q CHECKSUMS "$OUTD/NOTES.md"; then echo "NOTES.md still has CHECKSUMS" >&2; exit 1; fi
}

step_publish() {
    local notes=$OUTD/NOTES.md title assets=()
    need "$notes" "release notes"
    grep -q CHECKSUMS "$notes" && { echo "run finish first: NOTES.md has no checksums" >&2; exit 1; }
    title=$(head -1 "$notes" | sed 's/^# *//')
    for f in LightTouch-$V-android-arm64.apk LightTouch-$V-Linux-x86_64.AppImage LightTouch-$V-Windows-x86_64.zip \
             LightTouch-$V-macOS-arm64.zip armv6.itpack LightTouch-$V-source.tar.gz qemu-ios-$V-source.tar.gz \
             SHA256SUMS RELEASE-MANIFEST.json LICENSE.txt; do need "$OUTD/$f" "package"; assets+=("$OUTD/$f"); done
    (cd "$OUTD" && sha256sum -c --quiet SHA256SUMS)
    git -C "$REPO" push -q origin "$V"
    git -C "$QEMU_IOS" push -q origin "lighttouch-$V"
    local pre=(); [ "$LT_PRERELEASE" = true ] && pre=(--prerelease)
    (cd "$REPO" && gh release create "$V" --verify-tag "${pre[@]}" --title "$title" --notes-file "$notes" "${assets[@]}")
}

steps=("$@"); auto=0
if [ ${#steps[@]} = 0 ]; then steps=(prepare android windows linux macos finish); auto=1; fi
for s in "${steps[@]}"; do
    case "$s" in prepare|android|windows|linux|macos|finish|publish) ;; *) echo "unknown step: $s" >&2; exit 2 ;; esac
done
mkdir -p "$R"
for s in "${steps[@]}"; do
    if [ $auto = 1 ] && [ -f "$R/.done-$s" ]; then log "$s already done"; continue; fi
    log "$s"; rm -f "$R/.done-$s"
    "step_$s"
    date -u +%FT%TZ > "$R/.done-$s"
done
