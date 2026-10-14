# Integration and branch policy

`main` is the consolidated development branch for the desktop, Android and Linux
source. This reconciliation preserves the original desktop history and the
reviewed Android/Linux implementation; it is not a release or a claim that every
initial acceptance gate has passed.

## Reconciled lanes

The 2026-10-14 reconciliation accounts for every existing fork branch. Worker
histories are merged as ancestry after reviewing their remaining differences;
adapted cherry-picks and superseded experiments retain the canonical final
implementation rather than being applied again. Exact producer commits stay
reachable. No active worker branch or worktree was deleted or reset.

| Branch | Status after reconciliation |
| --- | --- |
| `main` | Consolidated integration branch; base new reviewed work here. |
| `port/android` | Retained port checkpoint and compatibility entry; the integrator keeps it synchronized with `main`, not a separate product. |
| `work/android-product` | Active Android source and target-test lane; reviewed checkpoint `a80142ca` reconciled. Historical frame-cache overlay instructions were superseded by current build/run documentation. |
| `work/android-runtime` | Active shared runtime/Linux lane; `c687df78` reconciled. Older diagnosis/operator variants were replaced by reviewed canonical workflows. |
| `work/android-pixel` | Active compatibility lane; `50d299ac` reconciled, including scoped physical Pixel renderer/shutdown evidence and the private 4 KiB importer-retirement correction. |
| `work/android-firmware` | Retained producer/provenance lane; `57a019fa` reconciled. Its patches have integration equivalents. Reuse only after explicit assignment. |
| `work/android-presentation` | Superseded presentation lane; `800f14e0` reconciled through adapted integration equivalents. Retained for history; do not base new independent work here. |
| `work/android-viewport-cancel` | Active graphics/input ownership lane; `2ae48cc0` reconciled. Current canonical cancellation code supersedes old variants; current acceptance plans replace the historical candidate25-only allocation text. |

The Android/native source at this reconciliation equals port checkpoint
`7a28b743` (frozen app implementation `f80def4a`). The additional Pixel text and
this policy are documentation only. APK26 target evidence belongs to its exact
recorded producer, not to all future descendants. The private 4 KiB snapshot is
not a completed AVD qualification. Gyro remains deferred and unqualified.

Reuse assigned lanes rather than creating a branch for each build. Periodically
push reviewed source checkpoints. Before retiring a redundant branch, confirm
its owner has no pending work and preserve exact shipped source references.
Do not force push, prune active worktrees or upload private firmware, SDKs,
credentials, captures or device evidence. New experimental work after the
checkpoints above requires its own review; ancestry reconciliation does not
automatically approve later lane commits.

The QEMU fork follows the same policy; see its `BRANCHES.md`.
