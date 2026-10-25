//! `lighttouch worker <args json>`: one device session in its own process. Opens and verifies the record, boots the
//! engine, copies each new frame into the UI's shared memory, plays guest audio, and speaks the line protocol in
//! `lighttouch_core::protocol` (commands on stdin, events on stdout, log on stderr).
use crate::audio;
use lighttouch_core::frames::{FrameMeta, FrameWriter};
use lighttouch_core::library::Library;
use lighttouch_core::protocol::{Command, Event, StatusEvent, WorkerArgs};
use lighttouch_core::runtime::{self, BootOptions, Device, Runtime, State};
use lighttouch_core::{monotonic_ns, process_cpu_ns, sys};
use std::io::{BufRead, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

pub fn main(args: &[String]) -> i32 {
    let parsed: WorkerArgs = match args.first().map(|a| serde_json::from_str(a)) {
        Some(Ok(parsed)) => parsed,
        _ => {
            eprintln!("usage: lighttouch worker '<WorkerArgs json>'");
            return 64;
        }
    };
    // The USB bridge listens before the engine starts (the engine dials it when the guest brings USB up), and its
    // address goes into the environment before this process has other threads.
    let usb = parsed.usb.as_ref().map(|usb| {
        let bridge = lighttouch_services::bridge::Bridge::start(&usb.daemon, &usb.pairing, &usb.log);
        if let Ok(bridge) = &bridge {
            set_engine_env("IT_USB_TCP", &bridge.guest_socket.to_string_lossy());
        }
        bridge
    });
    let events = spawn_event_writer(take_stdout());
    let commands = spawn_command_reader();
    match run(parsed, &events, commands, usb) {
        Ok(outcome) => {
            let _ = events.send(Event::Exit { clean: outcome.clean, dirty: outcome.dirty, message: outcome.message });
            drop(events);
            // Give the writer a moment to flush the exit event.
            thread::sleep(Duration::from_millis(50));
            if outcome.clean { 0 } else { 2 }
        }
        Err(message) => {
            eprintln!("session failed: {message}");
            let _ = events.send(Event::Exit { clean: false, dirty: true, message });
            drop(events);
            thread::sleep(Duration::from_millis(50));
            2
        }
    }
}

/// Set a variable the engine reads with `getenv`. On Windows that is the C runtime's own copy of the environment,
/// made at startup, which `SetEnvironmentVariableW` (Rust's `set_var`) doesn't reach: msvcrt's `_putenv_s` does,
/// and the engine shares this process's msvcrt.
fn set_engine_env(name: &str, value: &str) {
    // SAFETY: called before the engine and the session's threads start.
    unsafe { std::env::set_var(name, value) };
    #[cfg(windows)]
    {
        unsafe extern "C" {
            fn _putenv_s(name: *const std::ffi::c_char, value: *const std::ffi::c_char) -> i32;
        }
        if let (Ok(n), Ok(v)) = (std::ffi::CString::new(name), std::ffi::CString::new(value)) {
            unsafe { _putenv_s(n.as_ptr(), v.as_ptr()) };
        }
    }
}

struct Outcome {
    clean: bool,
    dirty: bool,
    message: String,
}

/// Keep stdout for the protocol and send everything else written there (the engine's `printf`s, which would land
/// in the middle of events) to stderr, the log. On Windows this also takes the C runtime's stdout off a pipe, where
/// it is fully buffered and the engine's lines never arrive.
fn take_stdout() -> std::fs::File {
    #[cfg(unix)]
    {
        use std::os::fd::FromRawFd;
        // SAFETY: called before the session's threads start; fd 1 and 2 are the standard streams.
        unsafe {
            let fd = libc::dup(1);
            assert!(fd >= 0, "dup stdout");
            libc::dup2(2, 1);
            std::fs::File::from_raw_fd(fd)
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::FromRawHandle;
        use windows_sys::Win32::System::Console::{GetStdHandle, STD_ERROR_HANDLE, STD_OUTPUT_HANDLE, SetStdHandle};
        // msvcrt, which the engine shares: its descriptors, and `stdout` as the second entry of its FILE table.
        unsafe extern "C" {
            fn _dup(fd: i32) -> i32;
            fn _dup2(from: i32, to: i32) -> i32;
            fn _get_osfhandle(fd: i32) -> isize;
            fn __iob_func() -> *mut u8;
            fn setvbuf(file: *mut u8, buffer: *mut u8, mode: i32, size: usize) -> i32;
        }
        const FILE_SIZE: usize = 48;
        const IONBF: i32 = 4;
        // SAFETY: called before the session's threads start; the duplicate descriptor stays open for the process's
        // life, so the handle it owns outlives the File.
        unsafe {
            let fd = _dup(1);
            assert!(fd >= 0, "dup stdout");
            let protocol = std::fs::File::from_raw_handle(_get_osfhandle(fd) as _);
            _dup2(2, 1);
            setvbuf(__iob_func().add(FILE_SIZE), std::ptr::null_mut(), IONBF, 0);
            SetStdHandle(STD_OUTPUT_HANDLE, GetStdHandle(STD_ERROR_HANDLE));
            protocol
        }
    }
}

fn spawn_event_writer(mut out: std::fs::File) -> Sender<Event> {
    let (tx, rx) = mpsc::channel::<Event>();
    thread::Builder::new()
        .name("events".into())
        .spawn(move || {
            for event in rx {
                if out.write_all(event.to_line().as_bytes()).and_then(|_| out.flush()).is_err() {
                    break;
                }
            }
        })
        .expect("spawn event writer");
    tx
}

/// Commands from the UI. A closed stdin means the UI went away: treat it as Stop.
fn spawn_command_reader() -> Receiver<Command> {
    let (tx, rx) = mpsc::channel::<Command>();
    thread::Builder::new()
        .name("commands".into())
        .spawn(move || {
            let stdin = std::io::stdin();
            for line in stdin.lock().lines() {
                let Ok(line) = line else { break };
                if line.trim().is_empty() {
                    continue;
                }
                match serde_json::from_str::<Command>(&line) {
                    Ok(command) => {
                        if tx.send(command).is_err() {
                            return;
                        }
                    }
                    Err(e) => eprintln!("ignoring malformed command ({e}): {line}"),
                }
            }
            let _ = tx.send(Command::Stop);
        })
        .expect("spawn command reader");
    rx
}

/// Wakes the frame thread from the runtime's publisher thread.
struct FrameSignal {
    pending: Mutex<bool>,
    ready: Condvar,
}

unsafe extern "C" fn frame_listener(opaque: *mut std::ffi::c_void, _sequence: u64) {
    let signal = unsafe { &*(opaque as *const FrameSignal) };
    *signal.pending.lock().unwrap() = true;
    signal.ready.notify_one();
}

fn run(
    args: WorkerArgs,
    events: &Sender<Event>,
    commands: Receiver<Command>,
    usb: Option<lighttouch_services::Result<lighttouch_services::bridge::Bridge>>,
) -> Result<Outcome, String> {
    let library = Library::open(&args.library).map_err(|e| format!("open the device library: {e}"))?;
    let record = library.record(&args.record).map_err(|e| e.to_string())?;
    eprintln!("session: {} ({} {}), recover={}", record.name, record.board, record.build, args.recover);

    // Admission: verify the device's files (fast when the digest cache is warm).
    // A previous session of this device may still be finishing its shutdown (its window closed moments ago):
    // wait for it briefly rather than failing.
    let busy_until = Instant::now() + Duration::from_secs(15);
    let device = loop {
        let mut last_report = Instant::now() - Duration::from_secs(1);
        let opened = Device::open(&library, &record.id, args.recover, &mut |done, total| {
            if last_report.elapsed() >= Duration::from_millis(100) || done == total {
                last_report = Instant::now();
                let _ = events.send(Event::Admission { done, total });
            }
            true
        });
        match opened {
            Err(e) if e.is(sys::LT_BUSY) && Instant::now() < busy_until => {
                std::thread::sleep(Duration::from_millis(250));
            }
            Err(e) if e.is(sys::LT_BUSY) => {
                return Err("This device is still in use by another LightTouch session. Stop it there, or wait \
                            for it to finish shutting down."
                    .into());
            }
            Err(e) if e.is(sys::LT_DIRTY) => {
                return Err("This device wasn't shut down cleanly. Recover it to start it again.".into());
            }
            Err(e) => return Err(format!("Couldn't open the device: {e}")),
            Ok(device) => break device,
        }
    };
    drop(library);

    let board = runtime::board_info(&args.engine, &record.board).map_err(|e| format!("engine: {e}"))?;
    let _ = events.send(Event::Board { info: board.clone() });
    let headphones = args.headphones && board.has(runtime::BoardInfo::HEADPHONES);
    let options = BootOptions {
        engine: args.engine.clone(),
        bootrom: record.bootrom_path.clone(),
        guest_package: record.guest_package_path.clone(),
        serial_log: Some(record.state_path.join("serial.log")),
        audio: args.audio,
        headphones,
    };
    let _bridge = match usb {
        Some(Ok(bridge)) => {
            let _ = events.send(Event::Usb { socket: Some(bridge.mux_socket.clone()), error: None });
            Some(bridge)
        }
        Some(Err(e)) => {
            eprintln!("usb: {e}");
            let _ = events.send(Event::Usb { socket: None, error: Some(e.to_string()) });
            None
        }
        None => None,
    };
    let runtime = Arc::new(Runtime::create(device, &options).map_err(|e| format!("Couldn't start the engine: {e}"))?);

    let mut writer = FrameWriter::open(&args.frames).map_err(|e| format!("frame memory: {e}"))?;
    let signal = Arc::new(FrameSignal { pending: Mutex::new(false), ready: Condvar::new() });
    let frames_done = Arc::new(AtomicBool::new(false));
    unsafe { runtime.set_frame_listener(Some(frame_listener), Arc::as_ptr(&signal) as *mut _) }
        .map_err(|e| e.to_string())?;
    let frame_thread = {
        let (runtime, signal, done, events) = (runtime.clone(), signal.clone(), frames_done.clone(), events.clone());
        thread::Builder::new()
            .name("frames".into())
            .spawn(move || {
                let mut since = 0u64;
                while !done.load(Ordering::Relaxed) {
                    {
                        let mut pending = signal.pending.lock().unwrap();
                        if !*pending {
                            pending = signal.ready.wait_timeout(pending, Duration::from_millis(100)).unwrap().0;
                        }
                        if !*pending {
                            continue;
                        }
                        *pending = false;
                    }
                    let mut published = None;
                    writer.publish(|pixels| match runtime.copy_frame(pixels, &mut since) {
                        Ok(Some(info)) => {
                            published = Some((info.sequence, info.timestamp_ns));
                            Some(FrameMeta {
                                width: info.width,
                                height: info.height,
                                stride: info.stride,
                                sequence: info.sequence,
                                timestamp_ns: info.timestamp_ns,
                                published_ns: 0,
                            })
                        }
                        Ok(None) => None,
                        Err(e) => {
                            eprintln!("frame copy: {e}");
                            None
                        }
                    });
                    if let Some((sequence, timestamp_ns)) = published {
                        let published_ns =
                            if timestamp_ns > 0 { timestamp_ns } else { lighttouch_core::monotonic_ns() };
                        let _ = events.send(Event::Frame { sequence, published_ns });
                    }
                }
            })
            .expect("spawn frame thread")
    };

    let audio = if args.audio {
        match audio::Output::open() {
            Ok(output) => Some(output),
            Err(e) => {
                eprintln!("audio unavailable: {e}");
                None
            }
        }
    } else {
        None
    };

    runtime.start().map_err(|e| format!("Couldn't start iOS: {e}"))?;
    let _ = events.send(Event::Started);

    let trace_input = std::env::var_os("LIGHTTOUCH_TRACE_INPUT").is_some();
    let mut pcm = vec![0i16; 4096 * 2];
    // The core reports LT_UNSUPPORTED (by a C++ throw) when the session runs without audio: ask once.
    let mut pcm_live = true;
    let mut stopping_since: Option<Instant> = None;
    let mut last_status: Option<(StatusEvent, Instant)> = None;
    let mut reported_input = 0u64;
    let mut pending_guest: Option<u64> = None;
    let mut ui_gone = false;
    let grace = Duration::from_millis(sys::LT_SHUTDOWN_GRACE_MS as u64);
    let outcome = loop {
        // Commands.
        loop {
            let command = match commands.try_recv() {
                Ok(command) => command,
                Err(TryRecvError::Empty) => break,
                // The UI went away: stop, once, and carry on polling so the shutdown completes.
                Err(TryRecvError::Disconnected) if ui_gone => break,
                Err(TryRecvError::Disconnected) => {
                    ui_gone = true;
                    eprintln!("the app closed; shutting down");
                    Command::Stop
                }
            };
            let name = command_name(&command);
            let result = match command {
                Command::Touch { contacts } => {
                    if trace_input {
                        eprintln!("touch {contacts:?}");
                    }
                    runtime.touch(monotonic_ns(), &contacts)
                }
                Command::Button { button, down } => {
                    if trace_input {
                        eprintln!("button {button} {}", if down { "down" } else { "up" });
                    }
                    runtime.button(button, down)
                }
                Command::Pause { paused } => {
                    if let Some(a) = &audio {
                        a.clear();
                    }
                    runtime.pause(paused)
                }
                Command::Stop => {
                    if stopping_since.is_some() {
                        Ok(())
                    } else {
                        stopping_since = Some(Instant::now());
                        runtime.request_shutdown()
                    }
                }
                Command::CancelInput => runtime.cancel_input(),
                Command::Motion { acceleration, gyro } => runtime.motion(monotonic_ns(), acceleration, gyro),
                Command::Screenshot { path } => runtime.screenshot_bmp(&path).map(|_| {
                    let _ = events.send(Event::Screenshot { path });
                }),
                Command::Guest { operation, argument, timeout_ms } => {
                    runtime.guest_request(operation, &argument, timeout_ms).map(|id| pending_guest = Some(id))
                }
                Command::Profile { path } => runtime.profile(path.as_deref()),
                Command::Mute { muted } => {
                    if let Some(a) = &audio {
                        a.set_muted(muted);
                    }
                    Ok(())
                }
            };
            if let Err(e) = result {
                let _ = events.send(Event::Error { command: name.into(), message: e.to_string() });
            }
        }

        // Touch admission results: a rejected batch releases everything.
        if let Ok((id, state, result)) = runtime.input_status() {
            if id != 0 && state != 1 && id != reported_input {
                reported_input = id;
                if state == 3 {
                    let _ = runtime.cancel_input();
                    let _ = events.send(Event::InputRejected {
                        message: format!("touch rejected by the engine ({result}); all contacts released"),
                    });
                }
            }
        }

        if let Some(id) = pending_guest {
            match runtime.guest_poll(id) {
                Ok(result) if result.state == 1 => {}
                Ok(result) => {
                    pending_guest = None;
                    let _ = events.send(Event::GuestResult {
                        request_id: id,
                        state: result.state,
                        status: result.guest_status,
                        output: result.output,
                    });
                }
                Err(e) => {
                    pending_guest = None;
                    let _ = events.send(Event::Error { command: "guest".into(), message: e.to_string() });
                }
            }
        }

        let status = runtime.poll().map_err(|e| format!("engine status: {e}"))?;
        let diagnostics = runtime.diagnose().unwrap_or_default();
        let event = StatusEvent {
            state: status.state,
            dirty: status.dirty,
            frames: status.frames,
            engine_ready: diagnostics.engine_ready,
            display_sleeping: diagnostics.display_sleeping,
            shutdown_phase: diagnostics.shutdown_phase,
            guest_shutdown_confirmed: status.guest_shutdown_confirmed,
            storage_failed: status.storage_failed,
            cpu_ns: process_cpu_ns(),
        };
        let changed = last_status.as_ref().is_none_or(|(previous, at)| {
            previous.state != event.state
                || previous.display_sleeping != event.display_sleeping
                || previous.engine_ready != event.engine_ready
                || previous.shutdown_phase != event.shutdown_phase
                || at.elapsed() >= Duration::from_millis(250)
        });
        if changed {
            let _ = events.send(Event::Status(event));
            last_status = Some((event, Instant::now()));
        }

        match status.state {
            State::Stopped => {
                break Outcome {
                    clean: status.guest_shutdown_confirmed && !status.storage_failed,
                    dirty: status.dirty,
                    message: if status.guest_shutdown_confirmed {
                        String::new()
                    } else {
                        "iOS stopped without confirming shutdown".into()
                    },
                };
            }
            State::Failed => {
                break Outcome {
                    clean: false,
                    dirty: true,
                    message: if status.storage_failed {
                        "iOS stopped unexpectedly: a storage write failed".into()
                    } else {
                        format!("iOS stopped unexpectedly (engine exit {})", status.engine_exit)
                    },
                };
            }
            _ => {}
        }

        // Audio.
        while pcm_live {
            match runtime.pcm(&mut pcm) {
                Err(e) if e.is(lighttouch_core::sys::LT_UNSUPPORTED) => pcm_live = false,
                Ok(0) | Err(_) => break,
                Ok(frames) => {
                    if let Some(a) = &audio {
                        if status.state == State::Running {
                            a.push(&pcm[..frames as usize * 2]);
                        }
                    }
                }
            }
        }

        if let Some(since) = stopping_since {
            if since.elapsed() > grace {
                break Outcome {
                    clean: false,
                    dirty: true,
                    message: "iOS didn't finish shutting down in time; its data is kept, recover it to start again"
                        .into(),
                };
            }
        }
        thread::sleep(Duration::from_millis(4));
    };

    frames_done.store(true, Ordering::Relaxed);
    signal.ready.notify_one();
    let _ = frame_thread.join();
    let _ = unsafe { runtime.set_frame_listener(None, std::ptr::null_mut()) };
    if let Some(a) = &audio {
        a.clear();
    }
    drop(audio);
    match Arc::try_unwrap(runtime) {
        // A failed or timed-out engine may refuse; the process exits either way and the state stays dirty.
        Ok(runtime) => {
            if let Err((_, e)) = runtime.destroy() {
                eprintln!("runtime destroy: {e}");
            }
        }
        Err(_) => eprintln!("runtime still referenced at exit"),
    }
    eprintln!("session ended: clean={} dirty={} {}", outcome.clean, outcome.dirty, outcome.message);
    Ok(outcome)
}

fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Touch { .. } => "touch",
        Command::Button { .. } => "button",
        Command::Pause { .. } => "pause",
        Command::Stop => "stop",
        Command::CancelInput => "cancel_input",
        Command::Motion { .. } => "motion",
        Command::Screenshot { .. } => "screenshot",
        Command::Guest { .. } => "guest",
        Command::Profile { .. } => "profile",
        Command::Mute { .. } => "mute",
    }
}
