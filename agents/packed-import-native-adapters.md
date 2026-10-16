# Packed import native adapters

Windows and macOS accept:

```
<native-frontend> finalize-import --stage <absolute-private-stage> --destination <absolute-absent-destination>
```

Windows additionally accepts `--validation-cache <absolute-private-cache>`.
Stage and destination must be distinct siblings. The command verifies the existing
stage, protects its files, and atomically renames without replacing a destination.
It performs no extraction or copying. Exit zero means publication succeeded.
Stdin `stop` (or EOF) cancels before publication; stdin must remain owned/open
while finalization is running. Cancellation is rechecked immediately before rename.
The caller owns stage cleanup. Failed protection/publication restores stage write
permissions, including Windows protected DACLs, so caller cleanup remains possible.

The n72 packed record requires `path=nand`, `format=ITNAND01`, lowercase SHA-256,
`pages` in 0...2097152, lowercase `source_listing_sha256`, and no `listing_sha256`.
Geometry is exactly 4096+64 bytes, four chip selects, 524288 pages per chip.
Admission checks the complete image checksum, exact length, the 8 MiB index,
record bounds, unique record IDs and reachability of every record. The bounded
bitset uses at most 256 KiB. Boot assembly checks structure and emits the file path
with the existing private writable overlay. n81 remains a directory of page files.
No device migration or identity conversion is introduced.

Validation:

- .NET SDK 8.0.426: 38 existing import assertions and 15 packed assertions pass.
- Windows Headless compiles with zero warnings/errors. Actual Windows finalizer
  DACL/publication/cancellation execution still requires the rebuilt helper on target.
- Native Mac Swift 6.3.3: HostRuntime compiles. Standalone probe executes the
  checked-in PackedNAND tests successfully. The CLT lacks `Testing`, so `swift test`
  cannot compile the test target; equivalent assertions ran as a standalone probe.
- Native Mac shared admission probe passes packed image and immutable output/identity
  digest assertions. Native finalizer probe passes protection, exclusive publication,
  existing-destination refusal and pre-publication cancellation preservation.
- Mac admission/finalizer/argument sources typecheck under Swift 6 and the shared
  target's actor-isolation settings against the existing native dependency modules.
  Probes exercise actual source bodies; the admission probe substitutes only its
  error-construction helper to avoid linking the whole FirmwareKit product.
- Native probe source/binaries: `/Users/blake/Documents/LightTouch-Packed-Native-Adapters01/`.
  Linux probe copies: `builds/windows-native/packed-native-adapters/` in private workspace.

Integration build routes (engine remains unchanged):

- Windows: `dotnet publish Windows/LightTouchWin.Headless -c Release -r win-x64 --self-contained true -o <new-output>`;
  stage the resulting helper through the existing `Windows/tools/stage-shared.py`
  packaging route. Existing SDK: private workspace `builds/windows-native/sdk8/dotnet`.
- Mac: export the integrated source to a fresh directory on `blake@192.168.1.54`,
  then `swift build --package-path <source> --scratch-path <new-scratch> --configuration debug --jobs 1 --disable-index-store -Xswiftc -j1 --product LightTouchDesktopHost`.
  This rebuilds HostRuntime/FirmwareKit and the native host, not the engine.
  `macOS/build-shared.py` packages this product plus LightTouchDevice using explicit
  existing engine, firmwarekit, Python runtime and vendor-contents providers.
  Retained prior app: `/Users/blake/Documents/LightTouch-Session-Final-20261016/LightTouch Shared Desktop.app`.
  Existing dependency modules/objects are under
  `/Users/blake/Documents/LightTouch-Shared-Desktop-Compile01/scratch/arm64-apple-macosx/debug`;
  prior source and package receipts are retained beside that build and under
  `/Users/blake/Documents/LightTouch-Shared-Desktop-Package13/`.

Shared macOS startup still uses the existing FirmwareBootAdmission stopped-state
route; it needs no format migration. Packed file admission lives in HostRuntime
and FirmwareKit PreparedBase so import and boot use the same structure check.
