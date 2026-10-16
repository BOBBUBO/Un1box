# Integration and branch policy

`main` is the sole maintained branch of this fork. The desktop UI, Android port,
firmware tools and reviewed platform fixes are consolidated here. This is a
development checkpoint, not a claim that every acceptance gate has passed.

## Current reconciliation — 2026-10-16

All former port/work branches and linked development worktrees have been retired.
Reviewed changes were integrated; original worker commits remain reachable in
`main` history, including adapted cherry-picks and superseded experiments.
The packed importer, native admission/finalizers, shared Rotate fix and asset
walkthrough are integrated. Windows packed NAND import is user-confirmed.

Only `main` remains on the fork. Upstream tracking refs and research archive tags
are retained as references. Build artifacts, logs and private guest assets remain
outside Git. Exact revisions in existing artifact receipts remain authoritative;
reconciliation does not change which source produced an older binary.

Use a temporary branch/worktree only for independent assigned work. Before
retiring it, reconcile useful changes, confirm its owner has finished, preserve
build outputs and verify its history is retained. Push reviewed checkpoints to
`main`; do not accumulate redundant remote branches.

Do not force push, delete unmatched work, upload private firmware/SDKs/saves or
reapply rejected experiments merely because they appear in retained history.
The QEMU fork follows the same policy; see its `BRANCHES.md`.
