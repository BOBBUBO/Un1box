# Shared desktop foundation and build entrypoint

Read [ARCHITECTURE.md](ARCHITECTURE.md) for current source adoption and planned
Tk/SDL host adapters. Linux uses the actual shared Tk launcher and SDL controls now. Existing Mac
Cocoa and isolated Windows WinForms previews do not yet adopt the shared launcher or
SDL guest window; their native compile success is not cross-platform UI parity.
Android retains its touch UI; APK orchestration remains a future entrypoint
adapter rather than an advertised output of this initial desktop foundation.

All commands plan only unless `--execute --receipt /private/fresh.json` is added.
Plans for another host show `host_compatible: false`; execution refuses them.
Use the same checkout/entrypoint over SSH on the native Mac. These commands do
not acquire private assets or include them in packages. Commit source first;
use the allocated finite compiler/packaging resource scope. Recipe-specific
required arguments and receipts still apply.

```sh
python3 Desktop/build.py --matrix
python3 Desktop/build.py --target linux-x86_64 --action build --output /private/linux-build -- \
  --qemu /private/committed/qemu-ios --evidence /private/linux-attempt --name lighttouch-desktop01
python3 Desktop/build.py --target linux-x86_64 --action stage --output /private/LightTouch.AppDir -- \
  --manifest /private/audited-runtime-inputs.json
python3 Desktop/build.py --target linux-x86_64 --action package --output /private/LightTouch.AppImage -- \
  --appdir /private/LightTouch.AppDir --tool /private/appimagetool --runtime /private/runtime \
  --evidence /private/seal01
python3 Desktop/build.py --target macos-arm64 --action engine --output /private/mac-engine -- \
  --qemu-source /private/committed/qemu-ios --python /opt/homebrew/bin/python3.13 \
  --openssl-prefix /opt/homebrew/opt/openssl@3 --jobs 2
python3 Desktop/build.py --target macos-arm64 --action bundle --output '/private/LightTouch Native Test.app' -- \
  --vendor-contents '/Applications/Light Touch.app/Contents' --engine /private/mac-engine/libqemu-arm.dylib \
  --scratch /private/mac-swift-build --jobs 2
```

Linux build delegates to the existing guarded Debian11 runner; prepare its pinned
public dependency trees first (see `Linux/packaging/README.md`). Staging/sealing
uses audited manifests and retains source/license obligations. Mac development
bundles require native macOS SDK/Swift and explicit trusted prebuilt dependencies
(see `macOS/README.md`); they are not notarized distributions. Windows and Intel
Mac routes fail explicitly while their shared adapters/recipes are unimplemented.
A successful dispatcher receipt records child exit and file artifact hashes;
keep underlying per-file/dependency/native-test receipts and matching source.
No build receipt establishes guest acceptance.

Small asset-free tests:

```sh
/usr/bin/python3 -m unittest discover -s Desktop/tests -p 'test_*.py' -v
/usr/bin/python3 -m unittest discover -s Linux/launcher/tests -v
# Isolated X11 display required for real Tk tests; no guest runs.
cmake -S Desktop -B /private/desktop-ui-tests -DLIGHTTOUCH_DESKTOP_UI_TESTS=ON
cmake --build /private/desktop-ui-tests --parallel 1
ctest --test-dir /private/desktop-ui-tests --output-on-failure
```

The native test renders only the toolbar to an in-memory SDL surface. That is
host UI evidence, not a software guest GPU backend or target guest acceptance.
