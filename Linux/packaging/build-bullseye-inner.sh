#!/bin/bash
# Runs only inside the bounded Debian11 container. Sources are mounted read-only.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
export MAKEFLAGS=-j2
: "${LIGHTTOUCH_QEMU_SOURCE:?}"
: "${LIGHTTOUCH_APP_SOURCE:?}"
: "${LIGHTTOUCH_BUILD_OUTPUT:=/build}"
Q=$LIGHTTOUCH_QEMU_SOURCE
A=$LIGHTTOUCH_APP_SOURCE
B=$LIGHTTOUCH_BUILD_OUTPUT
# Bullseye security packages have moved out of the live mirror. Use the signed
# official archive indices; their publication time is historical by design.
cat > /etc/apt/sources.list <<'EOF'
deb [check-valid-until=no] http://archive.debian.org/debian bullseye main
deb [check-valid-until=no] http://archive.debian.org/debian-security bullseye-security main
EOF
apt-get update
apt-get install -y --no-install-recommends build-essential pkg-config ninja-build \
    python3 python3-dev python3-venv python3-pip python3-tk git ca-certificates \
    curl patch bison flex gawk gettext libsdl2-dev libglib2.0-dev libpixman-1-dev \
    zlib1g-dev libegl1-mesa-dev libgl1-mesa-dev libfdt-dev autoconf automake libtool libusb-1.0-0-dev libssl-dev libcurl4-openssl-dev patchelf squashfs-tools desktop-file-utils
mkdir -p "$B/tool-wheels"
python3 -m pip download --dest "$B/tool-wheels" cmake==3.27.9 packaging==24.0 tomli==2.0.1
python3 -m pip install --no-index --find-links "$B/tool-wheels" cmake==3.27.9 packaging==24.0 tomli==2.0.1
python3 -m pip install --no-index "$Q/python/wheels/meson-1.10.0-py3-none-any.whl" "$Q/python/wheels/pycotap-1.3.1-py3-none-any.whl"
git config --global --add safe.directory "$Q"
git config --global --add safe.directory "$A"
# QEMU installs its qemu Python package editable and creates ignored egg-info.
# Clone only tracked source into the private build tree; retain exact commit and
# use read-only object alternates, leaving the supplied checkout untouched.
revision=$(git -C "$Q" rev-parse HEAD)
if [ ! -d "$B/qemu-source/.git" ]; then
    git clone --shared --no-checkout "$Q" "$B/qemu-source"
    git -C "$B/qemu-source" checkout --detach "$revision"
fi
git -C "$B/qemu-source" fetch "$Q" "$revision"
git -C "$B/qemu-source" checkout --detach "$revision"
[ "$(git -C "$B/qemu-source" rev-parse HEAD)" = "$(git -C "$Q" rev-parse HEAD)" ]
Q=$B/qemu-source
dpkg-query -W -f='${Package}\t${Version}\n' > "$B/debian-packages.tsv"
python3 -m pip freeze > "$B/build-python-packages.txt"
# Archives are hash-verified and data-filter extracted by the host preparation
# recipe. Bullseye's Python3.9 tarfile has no data-filter API; original dependency
# builders see the verified, already extracted trees and retain their input stamps.
python3 "$Q/contrib/portable/build-deps.py" "$B/deps-full"
python3 "$Q/contrib/portable/build-crypto-deps.py" "$B/crypto"
python3 "$Q/contrib/portable/build-engine.py" "$B/engine-cxx17-full" --cpp-standard=gnu++17 --prefix "$B/deps-full/prefix" --crypto-prefix "$B/crypto/prefix"
cmake -S "$A/Linux" -B "$B/frontend" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DLIGHTTOUCH_QEMU_SOURCE="$Q" -DLIGHTTOUCH_BUILD_TESTS=ON
cmake --build "$B/frontend" --parallel 2
ctest --test-dir "$B/frontend" --output-on-failure
python3 "$A/Linux/packaging/build-mobile-deps.py" "$B/mobile"
python3 "$A/Linux/packaging/collect-bullseye-runtime.py" --output "$B/runtime" \
    --frontend "$B/frontend/lighttouch-linux" --engine "$B/engine-cxx17-full/libqemu-arm.so" \
    --dependency "$B/deps-full" --dependency "$B/crypto" --dependency "$B/mobile"
printf '\nLIGHTTOUCH_BULLSEYE_BUILD_COMPLETE\n'
