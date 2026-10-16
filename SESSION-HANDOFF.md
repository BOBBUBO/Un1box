# Session checkpoint: 2026-10-16

The project is paused and incomplete. Both maintained forks use `main`; former
port/work branches and linked development worktrees were retired after retaining
all their commits in consolidated history. Upstream remotes and research archive
tags are retained. Do not resume work from an obsolete branch or worktree path.

The maintained desktop UI is one Python/Tk launcher and shared SDL guest-window
controls across Linux, Windows and macOS. Android keeps its touch UI. Start with
[Desktop/README.md](Desktop/README.md) and [Desktop/build.py](Desktop/build.py).
The WinForms preview and original Cocoa app are transitional/reference paths.

Useful current changes include consistent Windows archive identity checks and
live setup progress (`b5a7e07e`), independent saved devices from the same imported
system (`50927382`), Windows owned service transport/build recipes, and bounded
Foundation lifetimes in firmware preparation. Existing configurations/saves must
be preserved. Windows services have cross-linked providers but still need shared
loader/allocator/owned-endpoint wiring and actual Windows guest validation.
Mac shared host compilation and packaging do not establish guest/Apps parity.

The final Windows update also recognizes Python 3.13's private OWNER RIGHTS
ACL only after verifying current-user ownership. Earlier packages wrongly
rejected their own `SharedWindows/bases` folder. Update the whole Windows ZIP;
do not delete bases/saves or change folder permissions to work around this.

The subsequent Windows import refinement overlaps ordered decompression with
four bounded file writers, then parallelizes verification reads and per-file
read-only ACL updates. Phase timings are logged. Focused import tests pass on
Linux; this establishes correctness checks, not a measured Windows speedup.
Windows now also saves successful validation outside the immutable base and
reuses it on ordinary Starts after checking protected ownership, directory file
identity and the system lock. Existing imports get one four-reader check;
Advanced offers an explicit full recheck. This deliberately avoids rereading
NAND on every Start and does not detect manual NAND changes with unchanged
system lock. Keep that limit clear; full recheck remains available.

The QEMU fork consolidates Windows WGL/storage/worker support, desktop EGL
initialization diagnostics and the persistent symbolic-IR experiment. The cache
remains **disabled and unqualified** on Linux and Android; no cross-session guest
speed improvement is established. The rejected hybrid CPU experiment stays in
history/archive tags, outside production sources. Real guests still use TCG.

Remaining work includes shared Windows/macOS guest and Apps qualification,
required multi-touch/concurrent input-render-audio/lifecycle/persistence and
sustained Android acceptance, the complete actual page-size matrix, qualified
Linux/Android persistent cache, and the selected firmware/device matrix. Gyro is
explicitly deferred and unqualified. Measured QEMU worker CPU/RAM is not iOS CPU
or RAM telemetry. Build success is never a guest acceptance pass.

The private coordination workspace holds `GOAL.txt`, `CHECKLIST.md`,
`OVERVIEW.md`, `DECISIONS.md`, `CONTRACTS.md`, and
`agents/root-current-handoff.md`. Final packages/logs/component provenance are
collected in its `deliverables/session-wrap-20261016/`. Old Git metadata, commit
inventories and artifact relocation maps are in `session-wrap-20261016/`;
previous coordination is archived in
`coordination-history/2026-10-16-session-wrap/`. Private fixtures, failed trials,
builds, saves and virtual-device disks remain outside Git/public uploads.

Use focused checks for changed behavior and required build checks. Reuse valid
unchanged evidence; do not restart broad smoke/harness/grant-packet cycles for
minor edits. Do not invoke question tools, flash/reboot devices, wipe saves,
change security settings, dedicate a GPU, create PRs or force push.
