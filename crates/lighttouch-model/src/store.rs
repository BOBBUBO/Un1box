//! Legacy Store and app installs for the current device, independent of any UI toolkit. Network and guest
//! service work runs on plain threads and reports back through a channel that `tick` applies; a generation number
//! per kind of request drops answers that a newer request has replaced. Mirrors the Android StoreModel.
use crate::Waker;
use lighttouch_catalog::store::{self, StoreApp, StoreCopy, StoreError};
use lighttouch_catalog::{Catalog, Target, ipa};
use lighttouch_services::apps::{Guest, InstallProgress};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::Duration;

/// One install, from download to the guest listing the app.
#[derive(Debug, Clone, PartialEq)]
pub struct InstallJob {
    pub name: String,
    pub phase: String,
    pub progress: Option<f32>,
    pub error: Option<String>,
    pub done: bool,
    pub cancellable: bool,
    /// The guest is changing its apps: iOS shouldn't be stopped until it finishes.
    pub mutating: bool,
}

impl InstallJob {
    fn new(name: &str, phase: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            phase: phase.into(),
            progress: None,
            error: None,
            done: false,
            cancellable: false,
            mutating: false,
        }
    }

    pub fn active(&self) -> bool {
        !self.done && self.error.is_none()
    }
}

/// An app icon, fetched once.
#[derive(Debug, Clone)]
pub enum Icon {
    Loading,
    Ready(Arc<[u8]>),
    Missing,
}

enum Message {
    Guest { socket: PathBuf, result: Result<(Target, HashMap<String, String>), String> },
    Results { generation: u64, result: Result<Vec<StoreApp>, StoreError> },
    Copies { generation: u64, result: Result<Vec<StoreCopy>, StoreError> },
    Checked { generation: u64, result: Result<StoreApp, StoreError> },
    Job(InstallJob),
    Installed(HashMap<String, String>),
    Crash(String),
    Icon { url: String, bytes: Option<Vec<u8>> },
}

pub struct Store {
    /// The device record the store is for; its installed apps and target are that device's alone.
    record: String,
    pub target: Option<Target>,
    /// The target came from the running guest rather than the record's catalog entry.
    pub confirmed: bool,
    pub query: String,
    pub results: Vec<StoreApp>,
    pub searching: bool,
    pub show_incompatible: bool,
    pub message: String,
    /// The app whose versions are open.
    pub chosen: Option<StoreApp>,
    pub copies: Option<Vec<StoreCopy>>,
    pub selected: Option<i64>,
    /// Why the chosen copy can't be installed.
    pub problem: Option<String>,
    /// The chosen copy, checked against the guest and ready to install.
    pub candidate: Option<StoreApp>,
    /// Bundle id → version on the running guest.
    pub installed: HashMap<String, String>,
    pub job: Option<InstallJob>,
    pub crash: Option<String>,
    icons: HashMap<String, Icon>,
    guest_socket: Option<PathBuf>,
    cancel: Arc<AtomicBool>,
    search_generation: u64,
    check_generation: u64,
    tx: Sender<Message>,
    rx: Receiver<Message>,
    scratch: PathBuf,
    waker: Waker,
}

fn background(name: &str, work: impl FnOnce() + Send + 'static) {
    let _ = std::thread::Builder::new().name(name.into()).spawn(work);
}

/// Guest service failures in plain words.
fn service_error(e: impl std::fmt::Display) -> String {
    e.to_string()
}

pub fn byte_size(bytes: u64) -> String {
    if bytes >= 1 << 30 {
        format!("{:.1} GB", bytes as f64 / (1u64 << 30) as f64)
    } else if bytes >= 1 << 20 {
        format!("{:.1} MB", bytes as f64 / (1u64 << 20) as f64)
    } else {
        format!("{} KB", bytes / 1024)
    }
}

impl Store {
    pub fn new(scratch: PathBuf, waker: Waker) -> Self {
        let (tx, rx) = channel();
        Self {
            record: String::new(),
            target: None,
            confirmed: false,
            query: String::new(),
            results: Vec::new(),
            searching: false,
            show_incompatible: true,
            message: String::new(),
            chosen: None,
            copies: None,
            selected: None,
            problem: None,
            candidate: None,
            installed: HashMap::new(),
            job: None,
            crash: None,
            icons: HashMap::new(),
            guest_socket: None,
            cancel: Arc::new(AtomicBool::new(false)),
            search_generation: 0,
            check_generation: 0,
            tx,
            rx,
            scratch,
            waker,
        }
    }

    fn sender(&self) -> (Sender<Message>, Waker) {
        (self.tx.clone(), self.waker.clone())
    }

    pub fn installing(&self) -> bool {
        self.job.as_ref().is_some_and(InstallJob::active)
    }

    pub fn mutating(&self) -> bool {
        self.job.as_ref().is_some_and(|j| j.active() && j.mutating)
    }

    /// Whether app installs can reach a guest right now.
    pub fn guest_connected(&self) -> bool {
        self.guest_socket.is_some()
    }

    /// Follow the current device (`record`, with its board and build) and its running guest's usbmux socket.
    pub fn follow(&mut self, record: Option<(&str, &str, &str)>, socket: Option<&Path>) {
        let (id, board, build) = record.unwrap_or(("", "", ""));
        if id != self.record {
            self.record = id.to_string();
            self.installed.clear();
            self.confirmed = false;
            self.target = None;
            self.results.clear();
            self.chosen = None;
            self.crash = None;
        }
        if !self.confirmed {
            let next = Catalog::get().find_build(board, build).and_then(|f| Target::of(&f.product_type, &f.version));
            if next != self.target {
                self.target = next;
                self.results.clear();
                self.chosen = None;
                if self.target.is_some() {
                    self.search();
                }
            }
        }
        if socket != self.guest_socket.as_deref() {
            self.guest_socket = socket.map(Path::to_path_buf);
            self.installed.clear();
            self.confirmed = false;
            if let Some(socket) = socket {
                self.refresh_guest(socket.to_path_buf());
            }
        }
    }

    /// Ask the running guest for its exact model and iOS version, and the apps it has.
    fn refresh_guest(&self, socket: PathBuf) {
        let (tx, waker) = self.sender();
        background("store-guest", move || {
            // It attaches once iOS has started, which can take a while on a cold boot.
            let result =
                Guest::connect(&socket, Duration::from_secs(300)).map_err(service_error).and_then(|mut guest| {
                    let target = guest.target().map_err(service_error)?;
                    let apps = guest.installed().map_err(service_error)?;
                    Ok((
                        target,
                        apps.into_iter()
                            .map(|a| (a.bundle_id, a.short_version.or(a.version).unwrap_or_default()))
                            .collect(),
                    ))
                });
            let _ = tx.send(Message::Guest { socket, result });
            waker();
        });
    }

    pub fn tick(&mut self) {
        while let Ok(message) = self.rx.try_recv() {
            match message {
                Message::Guest { socket, result } => {
                    if self.guest_socket.as_ref() != Some(&socket) {
                        continue; // another guest answers by now
                    }
                    match result {
                        Ok((target, installed)) => {
                            self.installed = installed;
                            self.confirmed = true;
                            if self.target.as_ref() != Some(&target) {
                                self.target = Some(target);
                                self.search();
                            }
                        }
                        Err(e) if !self.installing() => self.message = format!("App services: {e}"),
                        Err(_) => {}
                    }
                }
                Message::Results { generation, result } if generation == self.search_generation => {
                    self.searching = false;
                    match result {
                        Ok(apps) => {
                            if apps.is_empty() {
                                self.message = "No apps found.".into();
                            }
                            self.results = apps;
                        }
                        Err(e) => self.message = e.to_string(),
                    }
                }
                Message::Copies { generation, result } if generation == self.check_generation => match result {
                    Ok(rows) => {
                        let preferred = self.chosen.as_ref().map(|a| store::preferred(a.ipa_id));
                        let first =
                            rows.iter().find(|c| Some(c.ipa_id) == preferred).or(rows.first()).map(|c| c.ipa_id);
                        if rows.is_empty() {
                            self.problem = Some("No version of this app runs on this device.".into());
                        }
                        self.copies = Some(rows);
                        if let Some(id) = first {
                            self.choose(id);
                        }
                    }
                    Err(e) => {
                        self.copies = Some(Vec::new());
                        self.problem = Some(format!("Couldn't load versions: {e}"));
                    }
                },
                Message::Checked { generation, result } if generation == self.check_generation => match result {
                    Ok(app) => self.candidate = Some(app),
                    Err(e) => self.problem = Some(e.to_string()),
                },
                Message::Results { .. } | Message::Copies { .. } | Message::Checked { .. } => {}
                Message::Job(job) => self.job = Some(job),
                Message::Installed(installed) => self.installed = installed,
                Message::Crash(text) => self.crash = Some(text),
                Message::Icon { url, bytes } => {
                    self.icons.insert(url, bytes.map(|b| Icon::Ready(Arc::from(b))).unwrap_or(Icon::Missing));
                }
            }
        }
    }

    pub fn search(&mut self) {
        let Some(target) = self.target.clone() else {
            self.message = "Choose a device first.".into();
            return;
        };
        self.search_generation += 1;
        let (generation, query) = (self.search_generation, self.query.clone());
        self.searching = true;
        self.message.clear();
        let (tx, waker) = self.sender();
        background("store-search", move || {
            let result = store::search(&query, &target);
            let _ = tx.send(Message::Results { generation, result });
            waker();
        });
    }

    /// The results to show: everything, or only what runs here.
    pub fn visible_results(&self) -> Vec<&StoreApp> {
        self.results.iter().filter(|a| self.show_incompatible || a.incompatibility().is_none()).collect()
    }

    /// Open an app's versions.
    pub fn open(&mut self, app: &StoreApp) {
        let Some(target) = self.target.clone() else { return };
        self.chosen = Some(app.clone());
        self.copies = None;
        self.selected = None;
        self.problem = None;
        self.candidate = None;
        self.check_generation += 1;
        let generation = self.check_generation;
        let (tx, waker) = self.sender();
        let app = app.clone();
        background("store-versions", move || {
            let result = store::versions(&app, &target);
            let _ = tx.send(Message::Copies { generation, result });
            waker();
        });
    }

    pub fn close(&mut self) {
        self.chosen = None;
        self.check_generation += 1;
    }

    /// Check one archived copy against this device.
    pub fn choose(&mut self, ipa_id: i64) {
        let (Some(target), Some(app)) = (self.target.clone(), self.chosen.clone()) else { return };
        self.selected = Some(ipa_id);
        self.problem = None;
        self.candidate = None;
        self.check_generation += 1;
        let generation = self.check_generation;
        let (tx, waker) = self.sender();
        background("store-copy-check", move || {
            let result = store::compatible_copy(ipa_id, &target).and_then(|found| {
                if found.bundle_id != app.bundle_id {
                    Err(StoreError::Failed("This copy belongs to a different app.".into()))
                } else {
                    Ok(found)
                }
            });
            let _ = tx.send(Message::Checked { generation, result });
            waker();
        });
    }

    /// Download and install the checked candidate.
    pub fn install_candidate(&mut self) {
        let Some(app) = self.candidate.clone() else { return };
        let Some(socket) = self.guest_socket.clone() else {
            self.message = "Start the device to install apps.".into();
            return;
        };
        let scratch = self.scratch.clone();
        let cancel = self.cancel.clone();
        self.run_install(&app.name.clone(), move |report| {
            let mut guest = Guest::connect(&socket, Duration::from_secs(30)).map_err(service_error)?;
            let target = guest.target().map_err(service_error)?;
            let mut job = InstallJob::new(&app.name, "Downloading");
            job.cancellable = true;
            job.progress = Some(0.0);
            report(job.clone());
            let ipa = store::download(&app, &target, &scratch, &mut |done, total| {
                job.phase = match total {
                    Some(total) => format!("Downloading {} of {}", byte_size(done), byte_size(total)),
                    None => format!("Downloading {}", byte_size(done)),
                };
                job.progress = total.map(|t| done as f32 / t.max(1) as f32);
                report(job.clone());
                !cancel.load(Ordering::Relaxed)
            })
            .map_err(|e| e.to_string())?;
            let result = install_prepared(&mut guest, &target, &ipa, Some(&app.name), report);
            if let Some(dir) = ipa.parent() {
                let _ = std::fs::remove_dir_all(dir);
            }
            result
        });
    }

    /// Check and install an IPA file the user chose.
    pub fn install_file(&mut self, path: PathBuf) {
        let Some(socket) = self.guest_socket.clone() else {
            self.message = "Start the device to install apps.".into();
            return;
        };
        let scratch = self.scratch.clone();
        let name = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| "IPA".into());
        self.run_install(&name, move |report| {
            let mut guest = Guest::connect(&socket, Duration::from_secs(30)).map_err(service_error)?;
            let target = guest.target().map_err(service_error)?;
            std::fs::create_dir_all(&scratch).map_err(|e| e.to_string())?;
            install_prepared(&mut guest, &target, &path, None, report)
        });
    }

    fn run_install(
        &mut self,
        name: &str,
        work: impl FnOnce(&mut dyn FnMut(InstallJob)) -> Result<HashMap<String, String>, String> + Send + 'static,
    ) {
        if self.installing() {
            return;
        }
        self.cancel.store(false, Ordering::Relaxed);
        self.job = Some(InstallJob::new(name, "Preparing"));
        let (tx, waker) = self.sender();
        let name = name.to_string();
        background("store-install", move || {
            let mut last = InstallJob::new(&name, "Preparing");
            let mut report = |job: InstallJob| {
                last = job.clone();
                let _ = tx.send(Message::Job(job));
                waker();
            };
            match work(&mut report) {
                Ok(installed) => {
                    let _ = tx.send(Message::Installed(installed));
                }
                Err(error) => {
                    let mut job = last.clone();
                    job.error = Some(error);
                    job.cancellable = false;
                    job.mutating = false;
                    let _ = tx.send(Message::Job(job));
                }
            }
            waker();
        });
    }

    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }

    pub fn dismiss_job(&mut self) {
        if !self.installing() {
            self.job = None;
        }
    }

    /// Read why the last app quit: the useful lines of the guest's newest crash report.
    pub fn show_last_crash(&mut self) {
        let Some(socket) = self.guest_socket.clone() else {
            self.crash = Some("Start the device to read its crash reports.".into());
            return;
        };
        self.crash = Some("Reading the device's crash reports…".into());
        let (tx, waker) = self.sender();
        background("store-crash", move || {
            let text = match Guest::connect(&socket, Duration::from_secs(30)).and_then(|mut g| g.last_crash()) {
                Ok(Some((name, body))) => format!("{name}\n\n{}", crash_summary(&body)),
                Ok(None) => "The device has no crash reports.".into(),
                Err(e) => format!("Couldn't read crash reports: {e}"),
            };
            let _ = tx.send(Message::Crash(text));
            waker();
        });
    }

    pub fn dismiss_crash(&mut self) {
        self.crash = None;
    }

    /// An app icon, fetched in the background the first time it is asked for.
    pub fn icon(&mut self, url: &str) -> Icon {
        if let Some(icon) = self.icons.get(url) {
            return icon.clone();
        }
        self.icons.insert(url.to_string(), Icon::Loading);
        let (tx, waker) = self.sender();
        let url = url.to_string();
        background("store-icon", move || {
            let bytes = store::icon(&url);
            let _ = tx.send(Message::Icon { url, bytes });
            waker();
        });
        Icon::Loading
    }
}

/// Check the IPA, install it, then return the guest's updated app list. Reports each step.
fn install_prepared(
    guest: &mut Guest,
    target: &Target,
    ipa_path: &Path,
    label: Option<&str>,
    report: &mut dyn FnMut(InstallJob),
) -> Result<HashMap<String, String>, String> {
    let label_or = |fallback: &str| label.map(str::to_string).unwrap_or_else(|| fallback.to_string());
    report(InstallJob::new(&label_or("IPA"), "Checking the app"));
    let copy = ipa_path.with_file_name(format!("install-{}.ipa", std::process::id()));
    let info = ipa::prepare(ipa_path, &copy, target).map_err(|e| e.to_string())?;
    let name = label_or(&info.name);
    let result = guest.install(&copy, &info.bundle_id, Some(info.unpacked), &mut |progress| {
        let mut job = InstallJob::new(&name, "Installing");
        match progress {
            InstallProgress::Uploading { sent, total } => {
                job.phase = "Copying to the device".into();
                job.progress = Some(sent as f32 / total.max(1) as f32);
            }
            InstallProgress::Submitted => job.mutating = true,
            InstallProgress::Installing { status, percent } => {
                job.mutating = true;
                job.phase = if status == "ExtractingPackage" { "Unpacking".into() } else { "Installing".into() };
                job.progress = percent.map(|p| p as f32 / 100.0);
            }
            // iOS's own steps are internal names; unpacking, most of a large app's time, shows how much has landed.
            InstallProgress::Unpacking { done, total } => {
                job.mutating = true;
                job.phase = format!("Unpacking {} of {}", byte_size(done), byte_size(total));
                job.progress = Some(done as f32 / total.max(1) as f32);
            }
        }
        report(job);
        true
    });
    let _ = std::fs::remove_file(&copy);
    result.map_err(service_error)?;
    let installed = guest
        .installed()
        .map(|apps| {
            apps.into_iter().map(|a| (a.bundle_id, a.short_version.or(a.version).unwrap_or_default())).collect()
        })
        .unwrap_or_default();
    let mut job = InstallJob::new(&name, "Installed. Find it on the Home screen.");
    job.progress = Some(1.0);
    job.done = true;
    report(job);
    Ok(installed)
}

/// The lines of a crash report that say why an app quit.
fn crash_summary(body: &str) -> String {
    const KEEP: &[&str] = &[
        "Process",
        "Date/Time",
        "OS Version",
        "Exception Type",
        "Exception Codes",
        "Crashed Thread",
        "Dyld Error Message",
        "  Symbol not found",
        "  Referenced from",
        "  Expected in",
        "Termination Reason",
        "Application Specific Information",
    ];
    let lines: Vec<&str> = body.lines().collect();
    let dyld = lines.iter().position(|l| l.starts_with("Dyld Error Message"));
    let mut kept: Vec<&str> = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let near_dyld = dyld.is_some_and(|d| i > d && i <= d + 3);
        if (KEEP.iter().any(|k| line.starts_with(k)) || near_dyld) && !kept.contains(line) {
            kept.push(line);
        }
    }
    if kept.is_empty() { body.chars().take(1500).collect() } else { kept.join("\n") }
}
