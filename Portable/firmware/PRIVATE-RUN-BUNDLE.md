# Private n72 run-bundle checklist

This is an operator checklist for a locally authorized, single-user transfer.
It contains no guest assets or asset download instructions. A package passing
these checks is an intact delivery, not a guest-acceptance or public-distribution
approval. Follow [Running the Android port](../../Android/RUNNING.md) for the UI
and [source distribution](SOURCE-DISTRIBUTION.md) for the firmware component.

## 1. Freeze the actual artifacts

Create a new private output directory. Never replace a previous delivered ZIP,
source kit, prepared base or recorded failed attempt. Do not name an APK26 bundle
from an APK25 binary or a provisional source inventory.

Record these values from the terminal producer receipts before packaging:

| Input | Required binding |
| --- | --- |
| Main APK | Exact SHA256, byte count, package, signer certificate, minimum API and packaged host ABIs |
| Product/native source | Actual Git revisions, source archive SHA256, declared test-source staging, compiled report SHA256 and real CMake producer directory |
| Engine per ABI | Actual producer revision, unstripped and packaged SHA256 mapping, matching notices and source-bundle manifest SHA256 |
| Firmware/native JNI | Actual per-ABI input-to-packaged mappings, dependency/toolchain stamps and matching component source kit |
| Guest inputs | Trusted owner inventory plus exact three-file sizes/hashes and board/firmware/package compatibility |

Require the final report's compiled/static-verification state and independently
check its main APK hash. A prebuild native inventory remains history even when
included in a final report. Different binary bytes need actual new mappings even
if the source is unchanged. Keep any later test-only APK/source overlay separate;
the ordinary run ZIP contains only the main APK.

For candidate26, final APK/engine/source-kit hashes must come from its own terminal
receipts. An earlier candidate26 engine, historical original relink, a Mac build,
or an APK25 target run cannot establish those bindings or target behavior.

## 2. Choose one clean, matching input route

For the compact n72 delivery, use the owner's verified clean reference
`n72ap-7E18.itbase`, its matching n72 boot ROM and matching ARMv6 `.itpack`.
Read the original handoff and validation receipt first. Record whether the base
is a release reference or a locally generated stock output. Keep these origins
distinct: a reference base does not inherit a generated-stock record's G6 identity.

Check the retained archive/package-format receipts and small lock/identity
metadata for board, firmware, geometry and declared package hashes. `.itbase`
uses the supported prepared container format; it is not a renamed outer ZIP or
raw NAND. A filename or matching CPU architecture alone is insufficient. Source
hash equality or old Mac acceptance is not current Android acceptance.

Use verified original inputs read-only. Exclude every live or retained writable
session, NAND overlay, writable NOR, Photos, preferences, logs, captures and phone
library record. Do not clear a dirty marker to manufacture a clean source. No
full NAND copy, new stock preparation or firmware download is needed for this
reference-container route.

An already generated native prepared directory is a different UI route: its
complete lock/identity and declared boot/NAND/NOR/package files must validate.
Do not invent a new container or substitute that directory under the compact
three-file instructions. Likewise n81 uses matching kboot/NAND/NOR/ARMv7 and
does not take an arbitrary n72 hardware ROM; it is outside this small n72 bundle.

## 3. Lay out one ZIP without duplicating input trees

Recommended archive names, with the actual candidate number substituted:

```text
LightTouch-CANDIDATE.apk
bootrom.bin
armv6.itpack
n72ap-7E18.itbase
README.txt
PROVENANCE.json
APK-NOTICES.json
SHA256SUMS
FirmwareSourceKit/...
```

The first four files run the prepared guest. The source kit is not needed to
execute it; source/notice obligations are an independent delivery concern.
Include the matching firmware source kit unchanged, its original README/LICENSE,
manifests and build recipe. Record separately the matching engine, runtime,
frontend, renderer and toolchain source/notice coverage and any remaining gaps.
The firmware kit alone is not complete APK corresponding source or an LGPL
replacement/relink demonstration. Preserve original notices; do not invent a
grant for excluded source or private Apple assets. See
[dependency coverage](../../Android/DEPENDENCY-NOTICES.md) and
[frontend notices](../../Android/FRONTEND-NOTICES.md).

If matching engine source/relink kits are supplied outside the run ZIP, record
their exact manifest hashes **and how this recipient can access them**. An
operator-only path or a repository URL that omits dependency sources and relink
inputs does not establish recipient access. For a local transfer, a separately
accessible source directory can avoid duplicating the run assets; verify that
access and retain the applicable source-delivery method. Do not describe a bare
manifest hash as delivery of the files it authenticates. Source/relink material
and an actually executed rebuild/relink are separate evidence claims.

`APK-NOTICES.json` should inventory exact embedded notice paths and bytes from
the actual APK. `PROVENANCE.json` binds the values in section1, the chosen base
origin and compatibility limits. Keep private asset hashes in the private bundle,
not public source. Use relative bundle paths and receipt digests; avoid copying
host cache paths, endpoints, device identifiers or raw producer logs into it.
Publish legal source separately only within the authorized source workflow;
never upload the private ZIP, assets or their private provenance to Git/cloud.

This privacy rule for the mixed archive and Apple assets adds no restriction to
the licenses of separable covered code. Retain recipients' redistribution and
modification rights under those licenses; do not require separate publisher
permission to exercise them.

Write directly from the four immutable input files and selected source-kit files
using streaming ZIP reads/writes with Zip64 enabled. Compression and temporary
staging must not require a second full extracted base. Use an explicit input
allowlist, not a recursive walk of a library, handoff, cache or work directory.
Exclude SDKs, stock IPSWs, signing keys, test APKs and runtime evidence. Only
read-only legal source archives may be reused from a previous kit; preserve their
bytes and bind new component correspondence without changing the old kit.

## 4. Put exact install/import/run steps in README.txt

Name the actual main APK and three inputs. Instruct the owner to extract the
outer ZIP first. Verify SHA256SUMS before installation/import. State the supported
host ABI/API and signer from the actual APK receipt, plus storage for downloaded
ZIP, extracted files, import staging, immutable imported base and growing state.
Do not promise a fixed small storage budget or instant import.

For ordinary use, install the main APK only. Optional owner-run command:

```sh
adb -s AUTHORIZED_DEVICE install -r LightTouch-CANDIDATE.apk
```

No device is selected or operated by this checklist. An update must have a
compatible signer; preserve existing app data before considering any alternative.
Do not prescribe uninstall, force-stop, flashing, root or security changes.

While the guest is stopped, perform these selections in order:

1. **ROM**: select `bootrom.bin`, wait for asset import completion.
2. **Guest tools**: select `armv6.itpack`, wait for completion.
3. **Import base**: select `n72ap-7E18.itbase`, wait for validated import, select
   the new record, then **Start**. Do not choose the outer ZIP or its enclosing
   folder through **Import prepared folder instead**.

Explain that import binds assets to a new immutable base/record and creates fresh
writable state. Selecting another ROM/package later does not rewrite an existing
record. An update does not migrate personal data from another phone. Start on the
same record reuses its state; re-importing is not a persistence test.

Document Home/Power, touch within the guest image, Pause/Resume and foreground
return. Explain the difference between a host **Screenshot** and a guest Saved
Photo. **Stop** requests orderly guest shutdown; a disappeared process, exit0 or
missing receipt alone does not establish clean storage. Unconfirmed Stop retains
dirty state. Explicit **Recover retained state** is a cold boot of retained data,
not proof of a clean halt. Never ask the owner to delete markers/overlay/NOR.

For a persistence check, create an identifiable guest change, obtain confirmed
clean shutdown/storage, ordinarily Start the same record in a fresh worker and
inspect that change. Preserve any failure and its artifact identity. Do not label
historical G6 preparation or another producer's persistence as a new candidate pass.

## 5. Final audit and handoff

Before delivering, retain a private command/result receipt for each item:

1. Recheck all input hashes against frozen producer and owner inventories. Verify
   APK signer/package/ABI/API, embedded notices, ELF/ZIP alignment, actual strip
   mappings and renderer selection using the actual candidate's static receipts.
2. Require an exact ZIP member allowlist, unique safe relative names, no symlinks,
   no encrypted entries and no source/writable-state/cache additions. Stream each
   member once to EOF to verify its ZIP CRC and SHA256. Check SHA256SUMS covers
   every file except itself, with no missing or extra members. Verify source-kit
   inner manifests/modes and exact candidate correspondence separately.
3. Recheck input inode/size/mtime and read-only modes after packaging; no input
   contents or old delivery changed. The owner must preserve originals read-only
   after extraction; ZIP metadata does not enforce immutability on every host.
4. Seal the outer archive and retain the exact output path, size and SHA256:

   ```sh
   sha256sum LightTouch-private-CANDIDATE.zip > LightTouch-private-CANDIDATE.zip.sha256
   ```

5. Review README against the actual UI/source revision and compatibility matrix.
   Mark device/API/page-size/GPU/provider results verified, failed or untested.
   Static16KiB alignment is not a16KiB guest pass; virtual graphics are not a
   physical Tensor/PowerVR qualification. No renderer fix, speedup, complete gate
   or clean shutdown is promised without that candidate's actual target evidence.
   Gyro/motion remains deferred. VM worker CPU time includes supporting threads;
   it is not genuine iOS CPU/RAM accounting.
6. Report source/build/relink coverage exactly. Validate-only is not a link;
   original relink is not rebuilt modified LGPL replacement; neither is a full
   source build. Verify any delivered source reference is actually available and
   matches the binary before relying on it. Leave unresolved whole-APK source
   or publication requirements explicit.

The owner can check an extracted delivery with `sha256sum -c SHA256SUMS` from
its directory. This establishes integrity against the supplied manifest, not the
manifest's independent authenticity or successful guest execution. Retain the
approved outer hash/producer receipt through a trusted local channel. Target
operations and later acceptance require their own authorized owner and evidence.
