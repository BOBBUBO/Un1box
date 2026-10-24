//! The session's private USB link: the bundled QEMU-only usbmuxd listens on `qemu.sock`, which the engine dials
//! when the guest brings its USB port up (`IT_USB_TCP`), and serves usbmux clients on `mux.sock`. Both sockets live
//! in an owner-only folder; pairing records persist per device record in `pairing`. The daemon must be listening
//! before the engine starts.
use crate::{Error, Result};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub struct Bridge {
    child: Child,
    dir: PathBuf,
    /// Where usbmux clients connect.
    pub mux_socket: PathBuf,
    /// What the engine dials (the value for `IT_USB_TCP`).
    pub guest_socket: PathBuf,
}

impl Bridge {
    /// Start the daemon and wait (up to 5 s) for both sockets. `log` receives its output.
    pub fn start(daemon: &Path, pairing: &Path, log: &Path) -> Result<Self> {
        if !daemon.is_file() {
            return Err(Error("Guest app services are not included in this build.".into()));
        }
        #[cfg(not(unix))]
        {
            let _ = (pairing, log);
            Err(Error("Guest app services are not available on this system yet.".into()))
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
            use std::os::unix::process::CommandExt;
            std::fs::DirBuilder::new().recursive(true).mode(0o700).create(pairing)?;
            std::fs::set_permissions(pairing, std::fs::Permissions::from_mode(0o700))?;
            // Socket paths are limited to ~100 bytes, so they go in a short temporary folder.
            let dir = std::env::temp_dir().join(format!("lighttouch-usb-{}-{:x}", std::process::id(), nonce()));
            std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
            let mux_socket = dir.join("mux.sock");
            let guest_socket = dir.join("qemu.sock");
            if let Some(parent) = log.parent() {
                std::fs::create_dir_all(parent)?;
            }
            let out = std::fs::File::create(log)?;
            let mut command = Command::new(daemon);
            command
                .arg("-f")
                .arg("-S")
                .arg(&mux_socket)
                .arg("-C")
                .arg(pairing)
                .arg("-P")
                .arg(dir.join("daemon.pid"))
                .env("USBMUXD_QEMU_ADDR", &guest_socket)
                .stdin(Stdio::null())
                .stdout(out.try_clone()?)
                .stderr(out);
            // Its libraries sit beside it or in ../lib (AppImage, Linux packages).
            if let Some(bin) = daemon.parent() {
                let mut paths = vec![bin.to_path_buf(), bin.join("../lib")];
                if let Some(existing) = std::env::var_os("LD_LIBRARY_PATH") {
                    paths.extend(std::env::split_paths(&existing));
                }
                if let Ok(joined) = std::env::join_paths(paths) {
                    command.env("LD_LIBRARY_PATH", joined);
                }
            }
            // The daemon goes when this process does, however it ends.
            #[cfg(target_os = "linux")]
            unsafe {
                command.pre_exec(|| {
                    libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                    Ok(())
                });
            }
            let child = command.spawn().map_err(|e| Error(format!("The guest USB bridge couldn't start: {e}")))?;
            let mut bridge = Self { child, dir, mux_socket, guest_socket };
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if let Ok(Some(status)) = bridge.child.try_wait() {
                    return Err(Error(format!("The guest USB bridge exited ({status}); see {}", log.display())));
                }
                if is_socket(&bridge.mux_socket) && is_socket(&bridge.guest_socket) {
                    return Ok(bridge);
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            bridge.stop();
            Err(Error("The guest USB bridge didn't open its sockets within 5 seconds.".into()))
        }
    }

    pub fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    pub fn stop(&mut self) {
        if self.alive() {
            #[cfg(unix)]
            unsafe {
                libc::kill(self.child.id() as i32, libc::SIGTERM);
            }
            let deadline = Instant::now() + Duration::from_secs(2);
            while self.alive() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(20));
            }
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
        // Only the files we expect; anything else stays as evidence.
        for name in ["mux.sock", "qemu.sock", "daemon.pid"] {
            let _ = std::fs::remove_file(self.dir.join(name));
        }
        let _ = std::fs::remove_dir(&self.dir);
    }
}

impl Drop for Bridge {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(unix)]
fn is_socket(path: &Path) -> bool {
    use std::os::unix::fs::FileTypeExt;
    std::fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_socket())
}

fn nonce() -> u64 {
    let mut bytes = [0u8; 8];
    let _ = openssl::rand::rand_bytes(&mut bytes);
    u64::from_le_bytes(bytes)
}
