# Session checkpoint: 2026-10-16

The broader project is paused and incomplete. Both maintained forks use `main`;
all temporary development branches and linked worktrees have been retired after
retaining reviewed changes and original worker history. Upstream refs and
research archive tags remain references. Start new work from current `main`.

## Delivered desktop refinement

The maintained desktop UI is one Python/Tk launcher and shared SDL guest-window
controls across Linux, Windows and macOS. Android keeps its touch UI. The
WinForms preview and original Cocoa app are transitional/reference paths.
See [Desktop/README.md](Desktop/README.md) and the numbered
[asset acquisition/setup guide](docs/ASSETS.md), linked from README.

All three desktop launchers now use the common bounded streaming `.itbase`
importer. n72 NAND pages go directly into one indexed read-only `ITNAND01` file;
n81 retains its few large NAND files. The original archive and manifest are
verified, then a narrow native finalizer validates disk bytes and publishes the
same staging directory without copying NAND again. Writable overlays remain
separate. Alpha users may reimport original archives; no migration was required.
One real Linux n72 streaming import took 4.637s, with 1.866s native finalization;
these are scoped measurements, not Windows/macOS timing claims.

Windows boot, rendering/audio, shutdown, startup UI, Legacy Store guest app
installation and the new packed NAND import are user-confirmed. The shared
Rotate fix turns video and pointer coordinates together; its actual Windows
retest remains open. Apps-enabled startup preserves the full OS environment,
fixing the NVIDIA display-create crash caused by dropping SystemRoot/PATH/TEMP.
Windows saves successful immutable-system validation for reuse on ordinary Start;
Advanced provides an explicit full recheck. Interrupted shutdown offers direct
Boot recovered state without discarding saved data.

Updated Windows ZIP, Linux AppImage and Apple Silicon macOS ZIP are collected
with exact component revisions, checksums and build logs in the private
workspace's `deliverables/session-wrap-20261016/`. Filenames contain
`packed-import`. Native Windows helper/Rotate worker, Linux frontend and Mac
host/helper were rebuilt; receipts identify reused engine/dependency providers.
Mac requires macOS 26 and uses development signing. Compilation/packaging does
not establish Mac shared guest/Apps qualification or new Linux packed guest
acceptance. Existing Linux Radeon guest/audio/local IPA evidence remains scoped.

## Provisional alpha release

`v0.0.1` collects the current Android APK, Linux AppImage, Windows ZIP and
Apple Silicon macOS ZIP as a provisional GitHub prerelease. See the
[release page](https://github.com/blake-lucas/LightTouch/releases/tag/v0.0.1)
for checksums, installation and exact component provenance. Packaged engines
and dependencies include retained providers; the release tag does not imply
every component was rebuilt from that tag. Android uses debug signing; Mac
uses development signing and requires macOS 26. Private guest assets are excluded.

## Remaining qualification

Real guest CPUs still use QEMU TCG. The persistent symbolic-IR experiment stays
**disabled and unqualified** on Linux/Android; no cross-session guest speedup is
established. Rejected CPU experiments remain history, outside production paths.
Gyro is deferred and unqualified. Host QEMU CPU/RAM counters are not iOS telemetry.

Remaining gates include actual shared Mac guest/Apps behavior, required
multi-touch/concurrent input-render-audio/lifecycle/persistence, sustained Android
acceptance, the complete actual page-size matrix, qualified persistent cache,
and the selected firmware/device matrix. Preserve existing acceptance evidence
without expanding its scope or manufacturing passes.

## Continuation

The private coordination workspace contains GOAL.txt, CHECKLIST.md, OVERVIEW.md,
DECISIONS.md, CONTRACTS.md and agents/root-current-handoff.md. Source notes,
failed attempts, screenshots, assets, saves and build outputs remain outside
public Git. Retired worker build outputs were preserved in their build parents;
retirement mappings and review receipts identify exact locations.

Use focused checks for changed behavior. Do not restart broad smoke/audit cycles
for minor edits, invoke question tools, flash/reboot devices, wipe saves, dedicate
a GPU, create PRs or force push. Resume broader implementation only when asked.
