# Android branch policy

`port/android` is the canonical Android and Linux integration branch. Reviewed
worker changes land there, usually by cherry-pick. `main` preserves the original
desktop lineage. Worker branches are source ownership lanes, not separate APKs or
alternative supported products.

## Lane status

| Branch | Purpose and status |
| --- | --- |
| `port/android` | Canonical integration; use this for current port development. |
| `work/android-product` | Active Android frontend, build and device-test work. |
| `work/android-runtime` | Active shared runtime and Linux acceptance work. |
| `work/android-pixel` | Active Android compatibility tests and fixes. |
| `work/android-firmware` | Retained firmware source history. Current patches have integration equivalents; preserve exact producer references before retirement. |
| `work/android-presentation` | Retained presentation history pending final patch and producer-reference review. |

The 2026-10-14 remote audit found seven branches including `main`, with no
backup or scratch branches. Retained branches are not additional integration
targets. Reuse an assigned lane instead of creating a branch for each attempt or
APK. Reactivate a retained lane explicitly before making new source changes.

## Closing a lane

Review all intended changes against `port/android`, including any unmatched
patches. A matching patch ID is useful evidence, not proof of complete semantic
integration. Confirm that the owner has no pending work, and preserve every exact
source revision used by published build/source receipts through canonical
ancestry or an intentional producer tag. Then retire the redundant remote branch
and its local worktree when safe. Do not reset or prune unrelated dirty worktrees.

Periodically push reviewed integration and active-lane checkpoints. Push no
private firmware, SDKs, credentials, screenshots or device evidence. Default
branch changes, force pushes and release claims require separate review.

The QEMU fork follows the same policy with `port/android-backend` as its
canonical integration branch; see its `BRANCHES.md`.
