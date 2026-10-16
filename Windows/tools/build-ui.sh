#!/usr/bin/env bash
# Cross-publish native Windows apphost plus WindowsDesktop runtime.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
dotnet=${1:?usage: build-ui.sh DOTNET OUTPUT}
out=$(realpath -m "${2:?fresh publish destination}")
if [[ -e "$out" ]]; then echo 'Refusing existing publish destination' >&2; exit 2; fi
export DOTNET_ROOT="$(dirname "$dotnet")"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 DOTNET_NOLOGO=1
export DOTNET_CLI_HOME="$(dirname "$out")/dotnet-home"
export NUGET_PACKAGES="$(dirname "$out")/nuget"
cd "$root/Windows"
"$dotnet" run --project "$root/Windows/LightTouchWin.Tests" -c Release --disable-build-servers -p:UseSharedCompilation=false
"$dotnet" publish "$root/Windows/LightTouchWin" -c Release -r win-x64 --self-contained true \
  --disable-build-servers -p:UseSharedCompilation=false -p:PublishSingleFile=false -o "$out"
