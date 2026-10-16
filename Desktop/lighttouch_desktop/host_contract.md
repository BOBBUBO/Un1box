# Launcher host interface

The shared `Launcher(window, args, host)` contains the actual Tk layout and user
workflow. `LinuxHost` is the first implementation; `MacHost` supplies a native
DeviceRuntime adapter using these exact widgets. This Python duck-typed
interface is internal, with no wire/firmware schema or on-disk migration.

Host values/factories:

- `font_family`: installed UI font; dialogs use Tk native file/message dialogs.
- `open_profiles(directory)`: existing library object with `devices`, `selected`,
  `save(profile)`, `persist()` and `new_state(id)`. It must preserve existing data,
  keep base/state bindings stable and enforce distinct writable identities.
- `inspect_base(path)`, `validate(profile, allow_dirty=...)`,
  `load_profile(path)`, `export_profile(path, profile)`, `display_title(...)`:
  metadata/admission and private-profile adaptation. Never treat metadata as a
  guest/runtime qualification. Native integrity admission still runs on Start.
- `create_session(application_log)`: session object implementing the existing
  asynchronous `start`, `poll`, `stop`, `metrics`, `record_error` and
  `diagnostic_report` behavior, plus lifecycle/status/log fields consumed by the
  UI. Start creates a fresh worker; Stop retains unconfirmed state and leases.
- `prepare(source)`: asynchronous bounded-memory archive inspection with
  `active`, `events`, `result`, `error`, `cancelled`, `start()`.
- `guest_command(args, profile, recover)` and
  `import_command(args, source, destination, sha256)`: native invocation adapter;
  shared widgets do not construct QEMU arguments, select libraries or shell out.
- `start_services(args, directory, identity)`: returns `(bridge, environment)` or
  `(None, None)` when unavailable. It owns safe cleanup on partial startup;
  shared UI retains close/active-operation coordination. Bridge provides
  `close()` and the existing Apps API. `apps_window(...)` creates the shared
  service window where implemented; unsupported hosts must report unavailable.
- `clean_exit(returncode, final_receipt)`: validates that host's strict native
  receipt. Exit0 alone is not clean shutdown. Never infer success on timeout.

Host/domain errors derive from ValueError, OSError or RuntimeError so shared UI
presents actionable errors and retains diagnostics. The Mac adapter maps the
existing profile ID into its own isolated DeviceInstance state root and keeps
the installed Cocoa library untouched. Stop writes to the owned native host's
stdin; that host owns native admission, a fresh helper, audio, shutdown and
reaping. Mac services/IPA and process counters are explicitly unavailable in
this first slice. Native Mac compilation/package/guest behavior remain
unqualified until target execution; synthetic host tests do not qualify them.
Windows implementation is pending; no fallback launches WinForms/Cocoa.

The shared `ManagedSession` in `session.py` owns fresh subprocesses, bounded
output queues, stdin Stop, reader retirement and strict terminal receipt
parsing. `MacSession` is a label-only subclass. Windows adapters can construct
`ManagedSession(application_log, storage=ops, label=...)`; an optional
`open_log(path, *, exclusive)` callback returns an owned binary file. It must
create a new log exclusively for Start, and securely append for application
errors. `strict_clean_exit` requires the full native shutdown/reaping receipt;
the session never kills by PID or publishes clean writable state itself.

`Profiles`, `Preparation` and the shared profile helper functions accept an
optional keyword `storage=ops`. This keeps all profile schemas, base metadata,
archive algorithms and identity/state rules shared. The narrow `StorageOps`
backend supplies:

- `ensure_directory(path)`: validate/create a private writable container.
- `open_regular(path, flags=os.O_RDONLY, mode=0o600)`: owned noninheritable file
  descriptor; reject symbolic links/reparse paths and special files. Writable
  logs must also reject multiple hard links.
- `create_temp(directory)`: exclusively created private `(fd, path)` in that
  container, never an unrelated or existing file.
- `flush_file(fd)` and `replace_and_sync(temp, destination)`: durable file
  contents and atomic durable publication. The Windows implementation must
  supply native write-through/handle-flush semantics; ignoring unsupported
  directory fsync is insufficient.
- `immutable_base(path, stat_result)`: explicit platform immutability admission;
  shared native complete integrity admission remains authoritative.

POSIX defaults retain no-follow/close-on-exec opens and file/directory fsync.
They fail closed on Windows, which must supply its native backend. Do not
monkeypatch shared globals or weaken checks when flags are unavailable. The
Windows backend owns reparse-point/container admission and durable publication
implementation. These host-contract tests do not qualify actual Windows APIs
or guest execution. Failed library publication retains the prior in-memory
selection as well as the prior library when replacement has not occurred.
