# Bounded 16 KiB Android guest retry

This Linux supervisor requires an explicit exclusive AVD allocation. Preparing
the tool or passing its synthetic tests is not an allocation or target result.
It refuses any existing `emulator-5554` device-list entry, including offline or
unauthorized entries, and refuses launch if enumeration fails. It explicitly
binds emulator port 5554. Every target Android command specifies that serial.
It launches one AVD in an isolated Xvfb as the current user, using fresh
sudo credentials, two cores, requested 2048 MB RAM, `-gpu swiftshader`, no window,
no snapshot and no audio. It never wipes data or changes the GPU/image/version.
Retained evidence showed the emulator raised RAM to 2560 MB; inspect the new
actual startup log rather than labeling requested RAM as actual RAM.

Use the retained successfully imported record. This tool neither imports,
selects, installs APKs nor starts a guest. Freeze the intended main/test APK and
verify their actual installed identity separately. Supply the selected record
ID from its import receipt, the SHA256 of the verified prepared base's
`device.lock.json`, and the verified ROM/package SHA256 values. If a prior
record.json hash exists, also supply `--record-sha256`. No private record IDs,
firmware paths, asset bytes or hashes are embedded in this source.

After the allocation, launch the supervisor as a foreground command in a
persistent execution session. Retain the running session ID and inspect both
supervisor and independent watchdog PIDs. Do not background it with nohup and
end the launching command: a retained attempt showed the command runner removed
both those processes while the emulator's separate session survived. The model
may continue other bounded commands after the execution tool returns a running
session. For example, with task-specific variables containing reviewed paths
and receipts:

```sh
python3 -u Android/scripts/supervise_16k_retry.py \
  --allocation-granted --user "$task_user" \
  --adb "$task_adb" --emulator "$task_emulator" \
  --avd LightTouch_API35_16K --record-id "$task_record" \
  --lock-sha256 "$task_lock_hash" --rom-sha256 "$task_rom_hash" \
  --guest-sha256 "$task_guest_hash" --output "$task_output"
```

The output directory must not exist. Before 240 seconds from supervisor start,
the device must return page size 16384, report boot complete, and retain one
system_server PID with functioning package/activity services for 30 seconds.
The record/state/base/assets must exist; selected identity, record schema and
source pins must match; the dirty marker must be absent. The tool checks actual
ROM/package bytes and lock hash without another asset copy. Dirty state fails
this normal-start recipe; explicit recovery is a different authorized action.
Native startup still performs its full integrity, lease and admission checks.
Path checks require an explicit remote presence/absence sentinel and readable
parent. A zero host exit from `adb exec-out` is not a remote command-success
guarantee: retained target evidence returned zero alongside a missing-marker
stat error. Missing, unknown and present are separate outcomes.

`ready.json` records only this service/record gate, with `uiGatePassed=false`,
`visualUIInspectionRequired=true` and `uiGateDeadlineSeconds=240`. It does not
launch a previously installed application. After that handoff the controller
verifies and installs the frozen matching main/test APKs, runs scoped
`am start -W` with a bounded 30-second timeout, then captures and views the
actual new application's Android UI by the original 240-second deadline.
Installation, foreground launch and image review do not reset either clock.
If this UI gate fails or misses that deadline, create `request-stop` and retain
the failure; do not start a guest. An older APK's foreground timeout is not a
new guest failure. The supervisor receipt is not UI, guest, provider or
rendering acceptance. Reuse
the existing record in a fresh unused worker, select the fixed debug ANGLE enum
before the first context, and foreground MainActivity before every
`am instrument --no-restart`. Do not repeat standalone GL probes. Retain actual
normal app UID, 16384 page size, exact packaged engine/library identities and
the 197 public address-owner lines. Allow actual same-worker guest readiness
up to 600 seconds, subject to the overall deadline. A logo or agent ALIVE does
not justify a Frontmost request or input test. Inspect recognizable Lock/Home
and Photos pixels before those stages. Surface/raw captures and any concurrent
media test remain independently reviewed evidence.

An independent forked OS process tracks only this emulator launcher's
descendants and their Linux start ticks. At 898 seconds it requests termination;
just before 900 it force-disposes remaining owned processes even if the supervisor or
interactive model turn stalls. PID reuse cannot confer ownership. No global
ADB server, other AVD, X session or physical phone is affected. This mechanism
bounds emulator resources; it does not establish orderly guest shutdown.

At 840 seconds, or when a controller creates `request-stop` in the private
output directory, the supervisor attempts a single production Stop. It reads
the current UI hierarchy and taps only a unique enabled LightTouch semantic Stop control;
there are no guessed coordinates. It observes the identified worker for up to
35 seconds within the remaining budget and extracts own-PID shutdown/error
receipts and dirty-marker status. If UI/Binder admission fails, the error is
retained and cleanup still proceeds. It never calls a blocking native status
method or changes markers to manufacture success. Missing receipts, forced
disposal and a disappeared PID alone are unconfirmed. Independently require
native terminal confirmation, correct record/state identity, dirty-marker
removal and persisted guest user state before claiming clean Stop/recovery.

Boot/service failure terminates this attempt without fallback. Continuous
Android logs begin at the first available device; command receipts retain
service/process and Android/host memory-pressure samples. Binary asset checks
retain lengths/hashes instead of embedding firmware bytes. At terminal, inspect
the exact owned PIDs and receipt before releasing the allocation. Keep all
outputs private and retain failed samples; no screenshot or fixture goes in Git.

## Focused checks and JNI build preparation

The supervisor tests use real hung synthetic host subprocesses, without Android
tools or an emulator. They check independent deadline disposal, disarming after
cleanup, start-tick ownership, reused-root exclusion and semantic Stop lookup:

```sh
python3 -m unittest discover -s Android/scripts/tests -p test_retry_supervisor.py -v
```

JNI normal/recovery routing uses one C++17 helper with a fixed boolean. Ordinary
`Native.start(root,id)` passes false; `Native.recoverStart(root,id)` passes true.
The Kotlin declarations and trailing Binder recovery operation match these
exports. Existing CMake applies C++17, debug definition `LT_ANDROID_DEBUG=1`,
the pinned NDK API29 targets for both ABIs, and 16384 maximum-page alignment
at link time. No new native compile definition or public Portable ABI is needed.

When a compiler allocation is granted, derive the syntax argv from the frozen
compile_commands.json entry ending in lighttouch_jni.cpp: replace its source
with the reviewed JNI source, remove `-o OBJECT` and `-c`, and append
`-fsyntax-only -Wall -Wextra -Werror`. Preserve all other target/sysroot/include
and debug flags; run arm64-v8a then x86_64 sequentially. The targets are
`aarch64-none-linux-android29` and `x86_64-none-linux-android29`; the pinned NDK is
28.2.13676358. Record the database and source hashes with command exits. For a
configured candidate build, `ninja -C PRIVATE_ABI_BUILD -j1 lighttouch_jni`
rebuilds the native adapter and its required dependencies; that requires its
own build allocation. Syntax success alone does not verify linking or packaging.

Shared Contracts.cpp already tests dirty refusal, explicit recovery and marker
preservation. The separate recovery UI and opt-in coldRecover test must also
be verified with the matching packaged JNI. No target recovery or input result
is inferred from this source audit or synthetic process test.
