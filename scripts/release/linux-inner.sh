#!/bin/sh
# The Linux release build, run by scripts/build-release.sh inside the Debian 11 (glibc 2.31) build
# image. /src/app and $QEMU_SOURCE are read-only release worktrees and /build the output. The prebuilt
# prefixes are mounted read-only at /build/{deps-full,crypto,mobile-private-runtime-ui15}, where they
# were built: their pkg-config files name those paths.
set -eu
LT_JOBS=${LT_JOBS:-4}
export LC_ALL=C.UTF-8 MAKEFLAGS=-j$LT_JOBS CMAKE_BUILD_PARALLEL_LEVEL=$LT_JOBS
git config --global --add safe.directory '*'
Q=$QEMU_SOURCE
[ "$(git -C "$Q" rev-parse HEAD)" = "$EXPECTED_QEMU_REV" ]
[ -z "$(git -C "$Q" status --porcelain=v1 --untracked-files=all)" ]
[ "$(git -C /src/app rev-parse HEAD)" = "$EXPECTED_APP_REV" ]
[ -z "$(git -C /src/app status --porcelain=v1 --untracked-files=all)" ]
# QEMU's configure writes into its source tree, so build from a private clone of the same commit.
rm -rf /build/qemu-source
git clone -q --shared --no-checkout "$Q" /build/qemu-source
git -C /build/qemu-source checkout -q --detach "$EXPECTED_QEMU_REV"
for p in berkeley-softfloat-3 berkeley-testfloat-3 keycodemapdb; do cp -a "$Q/subprojects/$p" /build/qemu-source/subprojects/; done
[ -z "$(git -C /build/qemu-source status --porcelain=v1 --untracked-files=all)" ]
echo "=== ENGINE (LDFLAGS=-ldl: glibc 2.31 keeps dladdr in libdl)"
LDFLAGS=-ldl python3 /build/qemu-source/contrib/portable/build-engine.py /build/engine --cpp-standard=gnu++17 \
    --prefix /build/deps-full/prefix --crypto-prefix /build/crypto/prefix
echo "=== FRONTEND"
cmake -S /src/app/Linux -B /build/frontend -G Ninja -DCMAKE_BUILD_TYPE=Release -DLIGHTTOUCH_QEMU_SOURCE=/build/qemu-source \
    -DLIGHTTOUCH_BUILD_TESTS=ON -DCMAKE_CXX_FLAGS=-DSDL_PIXELFORMAT_BGRX32=SDL_PIXELFORMAT_RGB888
cmake --build /build/frontend --parallel "$LT_JOBS"
echo "=== CTEST"
ctest --test-dir /build/frontend --output-on-failure
echo "=== COLLECT"
rm -rf /build/runtime
python3 /src/app/Linux/packaging/collect-bullseye-runtime.py --output /build/runtime --frontend /build/frontend/lighttouch-linux \
    --engine /build/engine/libqemu-arm.so --dependency /build/deps-full --dependency /build/crypto \
    --dependency /build/mobile-private-runtime-ui15
printf '\nLINUX_RELEASE_BUILD_COMPLETE\n'
