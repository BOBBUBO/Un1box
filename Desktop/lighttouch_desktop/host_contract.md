# Launcher host interface

The shared `Launcher(window, args, host)` contains the actual Tk layout and user
workflow. `LinuxHost` is the first implementation. This Python duck-typed
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
presents actionable errors and retains diagnostics. Windows/macOS implementations
are pending; no fallback adapter silently launches WinForms/Cocoa. A second host
must implement and test these semantics, including safe filesystem/IPC/lease
behavior, before its application advertises shared UI support.
