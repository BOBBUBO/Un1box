# Linux desktop launcher

This launcher uses the existing `lighttouch-linux` SDL frontend and shared QEMU
engine. Python 3.9+ and Tk 8.6 provide the device library and native file dialogs;
there is no browser runtime. Native admission still checks every prepared-base
and guest-package asset before boot. Setup metadata checks are not verification
or guest acceptance.

Prepared directories remain in place and must already be read-only. Writable
sources and prepared-base archives can be imported through the existing native
import command into a new immutable destination; archives require a known SHA-256.
The launcher never chmods or rewrites the source. This avoids redundant copies of
large, already verified extracted fixtures.

Choose **Import .itbase archive…** for a `.itbase` file; provide its verified SHA-256
and a writable parent folder for the imported copy. **Prepared base folder →
Choose folder…** selects an already-extracted base, so its folder-only browser
does not list archive files. **Copy base folder…** imports an extracted folder
when a separate immutable copy is needed. Read-only `.itbase`, `.itpack` and boot
asset source files are supported; select guest tools and boot assets with their
separate **Choose file…** buttons.

Each device profile references an absolute prepared-base path, a separate writable
state folder and its matching guest package. New state is created by the native
runtime. Existing state is retained; a dirty state requires explicit recovery.
The n81 prepared kboot route does not use a separate hardware boot ROM. The current
n72 route uses its matching boot asset. Config import accepts versioned launcher
profiles with relative paths resolved against the config file; raw Mac/QEMU boot
arguments need conversion rather than silent execution. Engine code is selected
by the installed application, never by an imported profile.

Each Start creates a new SDL process, whose existing native runtime execs a fresh
worker. Stop requests ordinary shutdown of that owned frontend; the launcher must
wait for its real exit and clean receipt. It must not shorten native shutdown
grace, silently recover, delete state, kill an unresponsive guest or automatically
restart. The library and private logs use XDG data storage outside the application
bundle. A live session disables configuration/import/start actions.

CPU execution remains TCG. Persistent translation reuse is separate shared-engine
work and is not currently advertised as implemented or qualified. Gyro is deferred.
The desktop display retains the SDL frontend's multi-touch, keyboard/mouse,
rotation, pause, audio and cancellation handling. Guest screenshots require the
actual guest Home+Power buttons; SDL screenshot capture is a host image.

## Development checks

```sh
/usr/bin/python3 -m unittest discover -s Linux/launcher/tests -v
```

No test executes a guest. A real Tk window screenshot and actual guest admission,
render/input/audio/lifecycle/clean restart remain required after a resource handoff.

The planned AppImage compatibility floor is x86_64 Linux with glibc 2.31, kernel
5.4 and an X11 display (including XWayland). Build all native dependencies and the
bundled Python/Tcl/Tk against that floor, then test actual distributions; packaging
newer-host binaries does not establish this compatibility. Native Wayland and
musl-only distributions are not yet qualified. GPU driver libraries remain host
provided; a package must preserve the engine's working EGL provider rather than
bundle a vendor driver. No software GPU fallback is enabled by the launcher.
