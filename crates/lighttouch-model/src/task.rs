//! Long operations (adding a device, downloading, preparing) on their own threads, with progress the UI can show
//! and a cancel flag the work checks.
use crate::Waker;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Progress {
    /// What is happening now, in plain words ("Copying the system").
    pub phase: String,
    pub done: u64,
    pub total: u64,
}

impl Progress {
    /// 0..1, or `None` while the total is unknown.
    pub fn fraction(&self) -> Option<f32> {
        (self.total > 0).then(|| (self.done as f64 / self.total as f64).clamp(0.0, 1.0) as f32)
    }
}

/// Handed to the work: report progress, check for cancellation.
#[derive(Clone)]
pub struct Reporter {
    progress: Arc<Mutex<Progress>>,
    cancel: Arc<AtomicBool>,
    waker: Waker,
}

impl Reporter {
    /// Update progress; returns `false` once the user cancelled.
    pub fn report(&self, phase: &str, done: u64, total: u64) -> bool {
        {
            let mut p = self.progress.lock().unwrap();
            if p.phase != phase || p.done != done || p.total != total {
                p.phase.clear();
                p.phase.push_str(phase);
                p.done = done;
                p.total = total;
            }
        }
        (self.waker)();
        !self.cancelled()
    }

    pub fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }
}

pub struct Task<T> {
    progress: Arc<Mutex<Progress>>,
    cancel: Arc<AtomicBool>,
    handle: Option<JoinHandle<Result<T, String>>>,
}

impl<T: Send + 'static> Task<T> {
    pub fn spawn(waker: Waker, work: impl FnOnce(&Reporter) -> Result<T, String> + Send + 'static) -> Self {
        let progress = Arc::new(Mutex::new(Progress::default()));
        let cancel = Arc::new(AtomicBool::new(false));
        let reporter = Reporter { progress: progress.clone(), cancel: cancel.clone(), waker: waker.clone() };
        let handle = std::thread::Builder::new()
            .name("task".into())
            .spawn(move || {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| work(&reporter)))
                    .unwrap_or_else(|_| Err("something went wrong (internal error)".into()));
                waker();
                result
            })
            .expect("spawn task thread");
        Self { progress, cancel, handle: Some(handle) }
    }

    pub fn progress(&self) -> Progress {
        self.progress.lock().unwrap().clone()
    }

    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }

    /// The result once the work has finished.
    pub fn take_result(&mut self) -> Option<Result<T, String>> {
        if self.handle.as_ref().is_some_and(|h| h.is_finished()) {
            self.handle.take().map(|h| h.join().unwrap_or_else(|_| Err("internal error".into())))
        } else {
            None
        }
    }
}
