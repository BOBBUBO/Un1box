# Native memory qualification without SwiftTesting

`firmware-memory-probe` is an optional standalone assertion executable for native
macOS CLT installations that cannot build the SwiftTesting test target. It links
the actual `FirmwareKit` library and calls production `N72NAND.write`,
`Preparer.digest`, `Preparer.nandListing` and the bounded Foundation scope helper
through a narrow qualification SPI. It does not copy those algorithms or replace
them with fixture implementations. No app, engine, helper guest, firmware corpus,
historical SDK or SwiftTesting framework is needed. Existing package source
dependencies remain pinned in `Package.resolved`.

Build the explicit product instead of `swift test`:

```
swift build --package-path Packages/FirmwareKit --scratch-path /private/build --jobs 2 -c release --product firmware-memory-probe --disable-automatic-resolution
```

Use an external finite supervisor and a fresh private output path for each run:

```
/private/build/release/firmware-memory-probe ordinary --out /private/evidence/ordinary
/usr/bin/time -l /private/build/release/firmware-memory-probe sparse --out /private/evidence/sparse
```

`ordinary` asserts exact page bytes, zero omission, short-read padding, frozen page
coordinates and counts across four writer chunks; caller-order concurrent page
and listing hashes; streamed-versus-whole sparse digest and callback byte count;
and return/original-error/missing-input behavior. It prints one JSON PASS report
only after every assertion succeeds. The output directory retains synthetic
inputs and pages for independent inspection. It refuses an existing output path.

`sparse` writes a synthetic 7 GiB logical sparse input, one nonzero 4096-byte page
per 4 MiB chunk (~7 MiB allocated input). It invokes the full production writer,
compares every emitted page and omitted-zero path, runs production concurrent
hashing, and checks 1792 volume pages + 50 metadata pages = 1842 files against the
independent frozen listing SHA256:
`d9911cab80831b8e8f40a1b5ce830352983294300e8de1b45a0c27a6945b070b`.

The executable asserts bytes/counts/errors, **not a memory threshold**. A separate
watchdog must record aggregate process RSS, live host headroom, free disk,
original deadline and full child retirement. `/usr/bin/time -l` is supplemental
process peak evidence. Compile success, assertions and measured memory are
separate results; none establishes prepared firmware or guest acceptance. No
baseline reduction percentage should be inferred without an equivalent control.

This source has not yet been natively compiled or executed on the target CLT.
Ordinary SwiftTesting tests remain available on hosts that provide that framework.
