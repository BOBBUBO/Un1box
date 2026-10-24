//! Developer commands, mainly for headless tests:
//!
//! ```text
//! lighttouch list
//! lighttouch import <.itbase | folder> [--sha256 HEX] [--name NAME] [--rom FILE] [--package FILE]
//! lighttouch prepare <firmware id> [--ipsw FILE] [--rom FILE] [--name NAME]
//! lighttouch run <record id> [--seconds N] [--recover] [--shot FILE.ppm] [--script FILE]
//! ```
//!
//! `run` starts a worker like the app does, prints its events, takes an optional screenshot of the last frame, then
//! shuts iOS down cleanly. A script line is `<seconds> <command json>`, e.g. `40 {"cmd":"button","button":0,"down":true}`.
use lighttouch_core::frames::{FrameReader, segment_name};
use lighttouch_core::library::{ImportSource, Library, sha256_file};
use lighttouch_core::paths::{DataDirs, Resources, data_root};
use lighttouch_core::protocol::{Command, Event, WorkerArgs};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Stdio};
use std::time::{Duration, Instant};

pub fn handles(command: &str) -> bool {
    matches!(command, "list" | "import" | "run" | "prepare")
}

pub fn main(args: &[String]) -> i32 {
    let dirs = DataDirs::new(data_root());
    let result = match args[0].as_str() {
        "list" => list(&dirs),
        "import" => import(&dirs, &args[1..]),
        "run" => run(&dirs, &args[1..]),
        "prepare" => prepare(dirs, &args[1..]),
        _ => unreachable!(),
    };
    match result {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("error: {e}");
            1
        }
    }
}

fn option(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn list(dirs: &DataDirs) -> Result<(), String> {
    let library = Library::open(&dirs.library()).map_err(|e| e.to_string())?;
    for r in library.records().map_err(|e| e.to_string())? {
        println!(
            "{}{} {:<28} {} {}{}{}",
            if r.selected { "*" } else { " " },
            r.id,
            r.name,
            r.board,
            r.build,
            if r.dirty { " (needs recovery)" } else { "" },
            if r.guest_package_path.is_some() { "" } else { " (no guest tools)" }
        );
    }
    Ok(())
}

fn import(dirs: &DataDirs, args: &[String]) -> Result<(), String> {
    let path = PathBuf::from(args.first().ok_or("import needs a path")?);
    let sha256 = match option(args, "--sha256") {
        Some(sha) => Some(sha),
        None if path.is_file() => {
            eprintln!("hashing {}", path.display());
            Some(sha256_file(&path).map_err(|e| e.to_string())?)
        }
        None => None,
    };
    let source = ImportSource {
        path,
        sha256,
        name: option(args, "--name"),
        bootrom: option(args, "--rom").map(PathBuf::from),
        guest_package: option(args, "--package").map(PathBuf::from),
    };
    let library = Library::open(&dirs.library()).map_err(|e| e.to_string())?;
    let started = Instant::now();
    let mut last = String::new();
    let record = library
        .import(&source, &mut |phase, done, total| {
            let line = format!("{phase} {}%", if total > 0 { done * 100 / total } else { 0 });
            if line != last {
                eprintln!("{line}");
                last = line;
            }
            true
        })
        .map_err(|e| e.to_string())?;
    println!("added {} ({}) in {:.1} s", record.name, record.id, started.elapsed().as_secs_f64());
    Ok(())
}

fn run(dirs: &DataDirs, args: &[String]) -> Result<(), String> {
    let record = args.first().ok_or("run needs a record id")?.clone();
    let seconds: f64 = option(args, "--seconds").and_then(|s| s.parse().ok()).unwrap_or(120.0);
    let shot = option(args, "--shot").map(PathBuf::from);
    let script = match option(args, "--script") {
        Some(path) => parse_script(&std::fs::read_to_string(&path).map_err(|e| format!("{path}: {e}"))?)?,
        None => Vec::new(),
    };
    let frames = FrameReader::create(&segment_name()).map_err(|e| format!("frame memory: {e}"))?;
    let services = args.iter().any(|a| a == "--services");
    let usb = if services { lighttouch_model::usb_args(dirs, &Resources::locate(), &record) } else { None };
    if services && usb.is_none() {
        return Err("--services needs usbmuxd-qemu (LIGHTTOUCH_USBMUXD)".into());
    }
    let worker_args = WorkerArgs {
        library: dirs.library(),
        usb,
        record,
        engine: Resources::locate().engine(),
        frames: frames.name().to_string(),
        recover: args.iter().any(|a| a == "--recover"),
        audio: !args.iter().any(|a| a == "--no-audio"),
        headphones: true,
    };
    let mut child = spawn_worker(&worker_args)?;
    let mut stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel::<Event>();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(event) = Event::parse(&line) {
                if tx.send(event).is_err() {
                    break;
                }
            }
        }
    });
    let started = Instant::now();
    let mut stop_sent = false;
    let mut script = script.into_iter().peekable();
    let mut frame_count = 0u64;
    let mut last_frame_log = Instant::now();
    let mut last_status_log = Instant::now();
    loop {
        while let Some((at, _)) = script.peek() {
            if started.elapsed().as_secs_f64() < *at {
                break;
            }
            let (_, command) = script.next().unwrap();
            send(&mut stdin, &command);
        }
        if !stop_sent && started.elapsed().as_secs_f64() >= seconds {
            if let Some(path) = &shot {
                save_frame(&frames, path)?;
            }
            eprintln!("[{:6.1}] requesting shutdown", started.elapsed().as_secs_f64());
            send(&mut stdin, &Command::Stop);
            stop_sent = true;
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(Event::Frame { .. }) => {
                frame_count += 1;
                if last_frame_log.elapsed() >= Duration::from_secs(10) {
                    eprintln!("[{:6.1}] {frame_count} frames", started.elapsed().as_secs_f64());
                    last_frame_log = Instant::now();
                }
            }
            Ok(Event::Status(status)) => {
                if last_status_log.elapsed() >= Duration::from_secs(10) {
                    eprintln!("[{:6.1}] status {:?}", started.elapsed().as_secs_f64(), status);
                    last_status_log = Instant::now();
                }
            }
            Ok(Event::Usb { socket: Some(socket), .. }) => {
                eprintln!("[{:6.1}] usb bridge at {}", started.elapsed().as_secs_f64(), socket.display());
                std::thread::spawn(move || probe_services(socket, started));
            }
            Ok(Event::Exit { clean, dirty, message }) => {
                eprintln!("[{:6.1}] exit clean={clean} dirty={dirty} {message}", started.elapsed().as_secs_f64());
                break;
            }
            Ok(event) => eprintln!("[{:6.1}] {}", started.elapsed().as_secs_f64(), event.to_line().trim_end()),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    let status = child.wait().map_err(|e| e.to_string())?;
    eprintln!("worker exited with {status}; {frame_count} frames");
    Ok(())
}

pub fn spawn_worker(args: &WorkerArgs) -> Result<Child, String> {
    let exe = lighttouch_core::paths::worker_exe()?;
    std::process::Command::new(exe)
        .arg("worker")
        .arg(serde_json::to_string(args).unwrap())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("start the session process: {e}"))
}

fn send(stdin: &mut ChildStdin, command: &Command) {
    let _ = stdin.write_all(command.to_line().as_bytes());
    let _ = stdin.flush();
}

fn parse_script(text: &str) -> Result<Vec<(f64, Command)>, String> {
    let mut lines = Vec::new();
    for line in text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')) {
        let (at, json) = line.split_once(' ').ok_or_else(|| format!("bad script line: {line}"))?;
        let at: f64 = at.parse().map_err(|_| format!("bad time: {line}"))?;
        let command: Command = serde_json::from_str(json).map_err(|e| format!("{e}: {line}"))?;
        lines.push((at, command));
    }
    Ok(lines)
}

/// Write the newest frame as a binary PPM.
fn save_frame(frames: &FrameReader, path: &PathBuf) -> Result<(), String> {
    let mut pixels = Vec::new();
    let meta = frames.read(0, &mut pixels).ok_or("no frame to save")?;
    let mut out = format!("P6\n{} {}\n255\n", meta.width, meta.height).into_bytes();
    for row in pixels.chunks(meta.stride as usize).take(meta.height as usize) {
        for px in row[..meta.width as usize * 4].chunks_exact(4) {
            out.extend_from_slice(&[px[2], px[1], px[0]]);
        }
    }
    std::fs::write(path, out).map_err(|e| e.to_string())?;
    eprintln!("saved {}x{} frame to {}", meta.width, meta.height, path.display());
    Ok(())
}

/// Prepare a device from an IPSW through the app's own pipeline, printing its progress.
fn prepare(dirs: DataDirs, args: &[String]) -> Result<(), String> {
    use lighttouch_model::prepare::{IpswSource, PrepareRequest};
    let firmware_id = args.first().ok_or("prepare needs a firmware id, e.g. n72ap-7E18")?.clone();
    let request = PrepareRequest {
        firmware_id,
        ipsw: option(args, "--ipsw").map(|p| IpswSource::File(PathBuf::from(p))).unwrap_or(IpswSource::Download),
        bootrom: option(args, "--rom").map(PathBuf::from),
        name: option(args, "--name"),
    };
    let mut model = lighttouch_model::Model::new(dirs, Resources::locate(), std::sync::Arc::new(|| {}));
    lighttouch_model::prepare::availability(&model.resources)?;
    model.prepare(request);
    let mut last = String::new();
    loop {
        model.tick();
        if let Some(finished) = model.take_finished() {
            match finished {
                lighttouch_model::Finished::DeviceAdded { name, id, timings, .. } => {
                    for (stage, seconds) in timings {
                        eprintln!("  {stage:<44} {seconds:7.1} s");
                    }
                    println!("added {name} ({id})");
                    return Ok(());
                }
                lighttouch_model::Finished::Failed { message, .. } => return Err(message),
            }
        }
        if let Some((_, progress)) = model.busy() {
            let percent = progress.fraction().map(|f| format!(" {:.0}%", f * 100.0)).unwrap_or_default();
            let line = format!("{}{percent}", progress.phase);
            if line != last {
                eprintln!("{line}");
                last = line;
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// Wait for the guest to attach over USB, then open a lockdown session and report what it says.
fn probe_services(socket: PathBuf, started: Instant) {
    use lighttouch_services::lockdown::Lockdown;
    use lighttouch_services::usbmux::Mux;
    let at = || started.elapsed().as_secs_f64();
    let mux = Mux::new(socket);
    let device = loop {
        match mux.devices() {
            Ok(list) if !list.is_empty() => break list[0].clone(),
            Ok(_) => {}
            Err(e) => eprintln!("[{:6.1}] services: {e}", at()),
        }
        if at() > 180.0 {
            eprintln!("[{:6.1}] services: the guest never attached", at());
            return;
        }
        std::thread::sleep(Duration::from_secs(1));
    };
    eprintln!("[{:6.1}] services: attached {} (id {})", at(), device.udid, device.id);
    for _ in 0..20 {
        match Lockdown::session(&mux, &device, Duration::from_secs(15)) {
            Ok(mut lockdown) => {
                let mut value = |key: &str| lockdown.string_value(key).unwrap_or_else(|e| format!("<{e}>"));
                eprintln!(
                    "[{:6.1}] services: session up: {} iOS {} ({}), \"{}\"",
                    at(),
                    value("ProductType"),
                    value("ProductVersion"),
                    value("BuildVersion"),
                    value("DeviceName")
                );
                return;
            }
            Err(e) => eprintln!("[{:6.1}] services: {e}", at()),
        }
        std::thread::sleep(Duration::from_secs(3));
    }
}
