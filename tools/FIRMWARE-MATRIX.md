# Selected firmware inventory

Prepare the **six upstream entries marked `available` first**, then the explicit
release representatives below to cover each iOS major. This is a selected
collection, not a bulk sweep of experimental/beta/untested entries. Upstream's
README says most experimental builds boot and pass its checks but have less use
than stable; untested separately means not run through its pipeline. Preserve
those labels. Neither upstream claim establishes acceptance for our products.

| Device | Board/build | iOS | Stock IPSW bytes | Prepared route |
| --- | --- | --- | ---: | --- |
| iPod touch 2G | n72ap-5G77a | 2.2 | 291,123,491 | SecureROM → NOR LLB → iBoot; NAND/NOR/GID |
| iPod touch 2G | n72ap-5H11a | 2.2.1 | 291,140,244 | SecureROM → NOR LLB → iBoot; NAND/NOR/GID |
| iPod touch 2G | n72ap-7E18 | 3.1.3 | 263,275,211 | direct iBoot; NAND/NOR/GID |
| iPad | k48ap-7B367 | 3.2 | 478,959,325 | default prepared iBoot; NAND/NOR/GID |
| iPad | k48ap-7B500 | 3.2.2 | 479,001,595 | default prepared iBoot; NAND/NOR/GID |
| iPad | k48ap-8C148 | 4.2.1 | 578,084,840 | default prepared iBoot; NAND/NOR/GID |

All six also require synthetic identity, the preparation lock, a matching guest
package and isolated writable state. N72 uses `bootrom_240_4`, including when
entering iBoot directly. K48's default iBoot path uses prepared iBoot/NOR/GID
files; do not request an unrelated hardware ROM. The actual lock and recipe are
the authority for a specific prepared artifact.

The six stable stock IPSWs total 2,381,584,706 bytes. Their reuse/preparation/target
qualification is tracked separately. N72 2.2/2.2.1 have **zero catalog estimates**;
this means unknown, not zero disk/time. Do not admit a preparation using those
zero estimates. Reuse the existing verified n72-7E18 baseline and n81-8C148 base
for the representative 4.x row;
no redundant preparation/export of that base is needed.
Gyro remains deferred/unqualified.

The second track has one explicitly selected release per iOS major; two overlap
with the stable cohort. The union contains eleven unique configurations.

| Major | Representative release | Upstream label | Existing asset |
| --- | --- | --- | --- |
| 1 | n45ap-4A102 / 1.1.4 | experimental | not located in scoped inventory |
| 2 | n72ap-5H11a / 2.2.1 | available | not located in scoped inventory |
| 3 | n72ap-7E18 / 3.1.3 | available | verified baseline reused |
| 4 | n81ap-8C148 / 4.2.1 | experimental | verified kboot base reused |
| 5 | n81ap-9B206 / 5.1.1 | experimental | not located in scoped inventory |
| 6 | n81ap-10B500 / 6.1.6 | experimental | not located in scoped inventory |
| 7 | n90ap-11D257 / 7.1.2 | experimental | not located in scoped inventory |

The website's original iPhone/iPod1 beta2/3 columns need qualification: current
upstream commit 7bf6684b marks those 34 IMG3 routes coming soon and refuses their
preparation; the S5L8900 recipes implement 1.x only. They are not selected here.
N81 uses kboot/NAND/writable NOR/overlay and armv7.itpack, without a separate
hardware ROM. N45 uses iBoot/NOR/NAND, bootrom_s5l8900 and armv6.itpack. N90 uses
its own kboot/NAND/NOR/configuration plus armv7.itpack. Never substitute one
board's boot assets for another's.

## Programmatic preparation without GUI automation

Upstream ships `firmwarekit create`, `fetch`, `unwrap`, `pack-base` and
`unpack-base`. Their CLI runs over SSH. A GUI-driven macOS Codex agent is not
required. Recipes can use the native helper's supervised one-shot emulated boots
for keybag creation or sealing without opening the app UI. Those are actual guest
processes and must be admitted/serialized separately. Preparation also uses
macOS disk-image/filesystem adapters; compiling Swift elsewhere does not port
those adapters. Existing release ARMv6/ARMv7 guest packages can be reused without
acquiring historical SDKs or rebuilding guest tools.

N72 2.x preparation currently has neither data-protection keybag nor seal boots.
It is therefore the proposed smallest missing stable-build pilot. K48 selected
builds can require seal and, for 4.2.1, keybag boots. No pilot was executed by this
inventory work.

## Read-only inventory tool

`tools/firmware-matrix.py` emits JSON to stdout. It never downloads, prepares,
mounts, boots, extracts or writes a configuration. Redirect results outside Git.

```sh
python3 tools/firmware-matrix.py enumerate \
  --catalog Portable/firmware/firmware-catalog.json \
  --guest-pack /private/guest-tools/armv6.itpack \
  --guest-pack /private/guest-tools/armv7.itpack > /private/selected-matrix.json

python3 tools/firmware-matrix.py inspect-index /private/n72ap-7E18.itbase \
  > /private/base-index.json

python3 tools/firmware-matrix.py plan \
  --catalog /private/pinned-catalog.json --id n72ap-5G77a \
  --ipsw /private/iPod2,1_2.2_5G77a_Restore.ipsw \
  --root /private/new-preparation-run \
  --firmwarekit '/Applications/Light Touch.app/Contents/MacOS/firmwarekit' \
  --guest-tools /private/guest-tools > /private/preparation-plan.json
```

`enumerate` defaults to `stable_first_and_representative_releases`, with six
stable rows first and then five additional representative rows. Use
`enumerate --stable-only` for the six-entry first cohort. `plan` refuses entries
outside the stable cohort and explicit representative whitelist.
Explicit `enumerate --all-catalog` retains a **historical read-only**
inventory for explaining upstream gaps; it does not expand preparation scope.
The checked upstream catalog at `18393c20` has SHA-256
`85df0d7e8b025688bd353f2abe6ed95f9c8fa4cd3b531c4ab2f033a196670c63`.
The installed Mac catalog hash is
`d83227ee3aa4ea509dfa09b0f68e8830b03312fcceeb18f60e85281501fdeed1`;
both select the same eleven IDs, including the six stable rows. Keep exact revision/catalog hashes in receipts.

The index command reads only the `.itbase` index and does not establish payload
integrity. Guest-package enumeration validates the complete bounded zlib stream
and reports manifest declarations plus the package hash. Internal payload
hash/ABI validation remains separate. Each selected entry matches exactly one
family in the inspected release packages. The stable cohort uses `n72-ios2`,
`n72-ios3`, `k48-ios3` or `k48-ios4`; representatives additionally use `n45-ios1`,
`k48-ios5`, `k48-ios6` and `k48-ios7`. Manifest declarations do not prove runtime behavior.

## Projection for the shared UI

`entries[]` contains `device`, `board`, `product_type`, `version`, `build`,
`prerelease`, `prerelease_number`, source size/hash, recipe/clock, boot contract,
and matching guest family/version/serial/package SHA-256. Preserve null beta
fields for releases. All eleven selected configurations are release builds. `cohort` distinguishes
stable from representative entries, and `representative_major` identifies the
1–7 coverage rows.

`immutable_base.reference`, `.sha256` and `.lock_sha256` start null. Optional
`--prepared-lock /actual/base/device.lock.json` attaches the actual base-directory
reference and observed lock digest with status `lock_metadata_only`; the base
content hash stays null. `prepared_guest_package` contains the lock-declared
family/version/serial/package hash. This is metadata observation, not payload
verification. Preparation and per-platform runtime acceptance remain
`not_assessed`; never overwrite independent existing acceptance with that default.
A metadata copy of a lock is not the base directory: pass its actual base path.

This projection is **not another persisted configuration schema**. The existing
native record maps admitted base bytes to `base_sha256`/`base_path`, instance name
to `display_name`, and an exclusive state directory to `state_path`. ROM/guest
references map to `bootrom_path`/`guest_package_path`. Linux's existing v1 profile
uses `name`, `base`, `state`, `bootrom` and `guest_package`. Keep its record ID
stable; launching selects a saved record without repeating file selection.

Several instances may select one immutable artifact, but must each have their
own synthetic identity, writable NOR/overlay, package verdict state, pairing data
and regenerated endpoints. Current native import still copies the base per
record; physical sharing/dedup remains separate native work. Do not claim this
tool implements it or create writable hardlinks between instances.

## Artifacts, storage and provenance

An `.itbase` packages prepared boot/NAND/identity/lock contents. An `.itpack`
contains guest loader/package families. Both use `ITPACK01`, but they are distinct
inputs. The Mac bundle's `guest.aar` is an Apple Archive, not an Android AAR.

Keep stock inputs content-addressed by catalog SHA-1, with independently measured
size/SHA-256/source receipt. Retain verified decrypt-cache manifests. Pin the
preparer/helper/engine/source/catalog, guest-package hash/family, options, fit
checks/warnings and output file/listing hashes. Prepared bases must be read-only;
state/endpoints are per instance. Never identify a prepared base using only the
stock IPSW hash. Different tools/options/identities can produce different bases.
Keep firmware, ROMs, guest packages and private receipts/captures outside Git and
public uploads. Store only distributable source/tooling in Git.

## Remaining portable routes and qualification

`Linux/launcher/model.py` and `Portable/src/Storage.cpp` currently admit n72/n81
only. Preparation is now firmwarekit for every route (2026-10-20); Thus the selected k48/n45/n90
routes still need typed portable boot/storage/identity/package admission and
appropriate host adapters. N81 later-major packages and services also require
version-specific validation, beyond the existing 4.2.1 reference. Collecting a base does not implement
those routes. Native macOS uses broader upstream recipes, but compilation alone
is not guest acceptance. Do not simply remove validators to claim new support.

Track source supplied/hash-verified, prepared/fit-checked, Mac reference boot,
portable admission and each target's real boot/render/input/audio/lifecycle/
clean-stop/fresh-worker persistence separately. Retain actual screenshots and
measured evidence. Required multi-touch is tested where the board supports it.
Host/synthetic/Mac reference observations never substitute for target passes.

## Private input and preparation tools

`firmware-input.py --config /absolute/private/input.json` acquires **one** exact
catalog input with verified HTTPS, bounded bytes/time and complete size/SHA-1
verification before final publication. It measures SHA-256, records each failed
source before trying the next and retains failed partials. An existing private
input can be supplied first. This tool never prepares or boots firmware.

`firmware-prepare.py` is a deliberately narrow macOS **n72ap-5G77a create-only**
supervisor. It is separate from the read-only catalog inventory, from packaging
and from runtime acceptance. Its invocation requires a reviewed absolute private
JSON config, `execution_authorized: true`, `phase: "create-only"`, an exact
`supervisor_sha256`, and `--authorize-config-sha256` matching those exact bytes.
The integrator owns allocation; this is not a prompt for additional user consent.

The config supplies private absolute `root`, new `staging`/`cache`/`service_state`
paths, `tool`/`ipsw`/`entry` and their `*_sha256` digests, `guest_tools`, a complete
flat `guest_tool_sha256` manifest, `seed`, and `resident_avd_pid`. No firmware,
decryption keys, private manifests or host-specific execution config belongs in
Git. See the private handoff packet for an exact pinned configuration.

`create_help_sha256` pins the actual CLI's `create --help` bytes. Every emitted
flag is checked against that output before launching preparation. The old pinned
0.2.0 release has no `--language` option; it uses its existing locale defaults.
Do not derive its CLI from a newer checkout sharing the same version number.
`run_name` accepts a reviewed single-use `create-supervised-NN` directory. A
separately allocated successor uses new output/cache/state/source/config paths;
it preserves the first failure and shares only verified immutable inputs.

The process refuses reused outputs and independently verifies inputs, directory
privacy, selected recipe and fresh 32 GiB disk/8 GiB free+inactive RAM admission.
It uses one original 600-second clock (540 work, through 585 cancellation, through 600
kill/reap/attachment checks), a 2 GiB owned-RSS watchdog and 20 GiB pilot-allocation
watchdog. The latter includes the already acquired input. Process observations
run each half second; disk observations run at most once every five seconds.
macOS watchdogs are sampled, **not hard memory/disk containment**; transient
overshoot and short-lived detached children between samples cannot be ruled out.

SIGTERM/SIGINT request cancellation without extending the clock. Firmwarekit's
own cancellation runs first; tracked PID/start identities and escaped descendants
are subsequently killed/reaped. Observers and image-detach commands have bounded
deadlines and their own retired process groups. Only actual attachments whose
image paths resolve inside the pilot root are eligible for detach. A failed
preflight never detaches images. The resident AVD must retain its identity.

Success requires exit0, the exact final done event, matching board/build/entry,
bootrom strategy, n72-ios2 guest family and source/package provenance, complete
NOR/GID/identity hashes, and the full NAND listing hash using upstream's sorted
`path sha256\n` algorithm. NAND count/read-only modes and fit dispositions are checked.
No second extracted base is made. Input pins are rehashed after output verification.
`CONTENT_VERIFIED_REVIEW_REQUIRED` deliberately does **not** establish reference
boot, target support or a clean guest shutdown. Fit details/warnings/logs and
cleanup identities remain in the private `create-supervised-NN/RECEIPT.json`.

Synthetic tests exercise real owned subprocess success, original deadlines,
escaped-child retirement, observer timeout, preservation of previous receipts,
unrelated mount exclusion and corrupted output rejection without any firmware.

The original 2 GiB create02 watchdog run remains FAILED, even though its retained
output hashes match. It lacks a final done event and clean successful command
receipt. Later verifier changes cannot reclassify that operation.

Upstream `FitCheck.Log.check` throws for required negative fits and records/warns
optional negatives. Its persisted wire has only `piece`, `fits` and `proof`, so a
negative is not automatically a fatal required failure. `Pieces.checkBootArgs`
requires `amfi_allow_any_signature`; `cs_enforcement_disable` is explicitly optional
and may remain inert with a warning. This narrow supervisor admits only that exact
optional reason with its matching actual warning event. It retains `fits:false`
and `OPTIONAL_INERT_WARNING_UNQUALIFIED`. Required AMFI must have a positive record;
unknown negatives, malformed/duplicate records, missing warning or a different
reason are held for review. Other optional upstream capabilities are not silently
accepted by this pilot policy.

Resource profile `n72-create03-4gib` is a separately reviewed **proposal**, never an
automatic retry: 4 GiB sampled preparer RSS, existing 8 GiB free+inactive admission,
and a new 4 GiB free+inactive host reserve sampled every five seconds. Original
time/disk/process limits and resident AVD identity checks remain. The controller
now durably stores exact process identities/count/RSS and any violation before
raising, including reserve/disk failures. Watchdogs remain sampled rather than
hard reservations. A new reviewed config/source/run and original clock are needed
for a new phase; no hidden limit escalation occurs within an existing run.
