# Native status snapshot

`Native.status()` preserves its eight string prefix entries: state, detail,
diagnostic, engine log path, QMP path, provider name, provider selection fixed,
and touch trace configured. Five entries follow in this fixed order:

| Index | Field |
| --- | --- |
| 8 | poll_snapshot_valid |
| 9 | guest_shutdown_confirmed |
| 10 | dirty |
| 11 | storage_failed |
| 12 | engine_exit |

All five describe the same existing `lt_runtime_poll` invocation under the JNI
control guard. Valid is `1` only with an active runtime and an `LT_OK` result.
Absent or failed polls have valid `0`; their initialized field values cannot
confirm shutdown. The state prefix retains its existing failure remapping.

A clean terminal receipt requires stopped state, valid `1`, confirmed `1`,
dirty `0`, storage failed `0`, and engine exit `0`. No extra observations,
engine phase poll, or public runtime API are introduced by these entries.
