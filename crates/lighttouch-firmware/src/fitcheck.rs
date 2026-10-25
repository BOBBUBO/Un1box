//! FitCheck: proof, read off the firmware itself at prepare time, that a guest-side piece fits the firmware it is
//! injected into (Packages/FirmwareKit/Sources/FirmwareKit/FitCheck, GLIDispatchCheck, Portability/MachOHeaders).
//! Every check returns a Fit: fits with the proof, or does not fit with why. "Could not tell" is never a fit.
//!
//! ```ignore
//! let fw = Firmware::new(ed.snapshot()?, "armv7", kernel);
//! let f = fitcheck::loads("it_pbd", &helper_bytes, &fw, None);   // f.fits, f.proof
//! log.check(f, true, "left out")?;
//! ```
//!
//! STUB: signatures fixed for SystemEdits and the recipes; bodies are being ported.
use crate::error::Result;
use crate::hfs::Editor;
use crate::shared_cache::DyldSharedCache;
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Fit {
    pub piece: String,
    pub fits: bool,
    pub proof: String,
}

impl Fit {
    pub fn new(piece: impl Into<String>, fits: bool, proof: impl Into<String>) -> Fit {
        Fit { piece: piece.into(), fits, proof: proof.into() }
    }
    /// The warning event's text: the misfit and what the preparer did about it.
    pub fn warning(&self, outcome: &str) -> String {
        format!("{} does not fit this firmware ({outcome}): {}", self.piece, self.proof)
    }
}

/// The prepare's record of every check (the lock's "fit"). A required piece that does not fit fails; an optional
/// one is recorded and warned about, and the caller leaves it out.
pub struct Log {
    pub fits: RefCell<Vec<Fit>>,
    warn: Box<dyn Fn(&str)>,
}

impl Default for Log {
    fn default() -> Log {
        Log::new(|_| {})
    }
}

impl Log {
    pub fn new(warn: impl Fn(&str) + 'static) -> Log {
        Log { fits: RefCell::new(Vec::new()), warn: Box::new(warn) }
    }
    /// `outcome`: what the preparer does with an optional piece that does not fit ("left out", "kept: ...").
    pub fn check(&self, f: Fit, required: bool, outcome: &str) -> Result<bool> {
        self.fits.borrow_mut().push(f.clone());
        if f.fits {
            return Ok(true);
        }
        if required {
            let mut e = crate::unsupported!("{} does not fit this firmware: {}", f.piece, f.proof);
            e.piece = Some(f.piece.clone());
            return Err(e);
        }
        (self.warn)(&f.warning(outcome));
        Ok(false)
    }
    /// One optional piece that goes wherever it fits: every place is recorded, and only when it fits none is each
    /// misfit warned about. Returns the places' fits.
    pub fn check_any(&self, places: Vec<Fit>, outcome: &str) -> Vec<bool> {
        let any = places.iter().any(|f| f.fits);
        if !any {
            for f in &places {
                (self.warn)(&f.warning(outcome));
            }
        }
        let out = places.iter().map(|f| f.fits).collect();
        self.fits.borrow_mut().extend(places);
        out
    }
    /// A piece the preparer did not install (nothing proven, so not a fit; no warning).
    pub fn not_installed(&self, piece: &str, why: &str) {
        self.fits.borrow_mut().push(Fit::new(piece, false, format!("not installed ({why})")));
    }
    pub fn object(&self) -> serde_json::Value {
        serde_json::to_value(&*self.fits.borrow()).unwrap_or_default()
    }
}

/// The pristine firmware as the checks read it: a read-only view of the system volume (symlinks resolved inside
/// it), its dyld shared cache, and the decrypted kernelcache when the recipe has one.
pub struct Firmware {
    pub root: Editor,
    pub arch: String,
    pub kernelcache: Option<Vec<u8>>,
    #[allow(dead_code)]
    memo: RefCell<HashMap<String, Option<HashSet<String>>>>,
}

impl Firmware {
    pub fn new(root: Editor, arch: &str, kernelcache: Option<Vec<u8>>) -> Firmware {
        Firmware { root, arch: arch.to_string(), kernelcache, memo: RefCell::new(HashMap::new()) }
    }
    /// The file at `rel` (with or without a leading "/"), symlinks followed inside the volume.
    pub fn data(&self, rel: &str) -> Option<Vec<u8>> {
        self.root.read(rel.trim_start_matches('/')).ok()
    }
    /// The volume's dyld shared cache for `arch`, if any.
    pub fn cache(&self) -> Option<&DyldSharedCache> {
        todo!("FitCheck.Firmware.cache")
    }
    /// Which of the firmware's own executables carries each dyld-required load command (LC_REQ_DYLD set).
    pub fn precedent(&self) -> &HashMap<u32, String> {
        todo!("FitCheck.Firmware.precedent")
    }
}

/// LC_DYLD_INFO_ONLY: a firmware whose dyld has no precedent for it takes the legacy-linked helpers.
pub const LC_DYLD_INFO_ONLY: u32 = 0x8000_0022;
/// OpenGLES.framework/OpenGLES, volume-relative (FitCheck.openGLES).
pub const OPENGLES: &str = "System/Library/Frameworks/OpenGLES.framework/OpenGLES";
/// it_prefs' settings: (domain, key) pairs (FitCheck.itPrefs).
pub const IT_PREFS: &[(&str, &str)] = &[];

pub fn loads(piece: &str, bin: &[u8], fw: &Firmware, host: Option<&str>) -> Fit {
    let _ = (piece, bin, fw, host);
    todo!("FitCheck.loads")
}
pub fn msm_quiet(fw: &Firmware, program: &str, dylib: &[u8]) -> Fit {
    let _ = (fw, program, dylib);
    todo!("FitCheck.msmQuiet")
}
pub fn prefs(fw: &Firmware, settings: &[(&str, &str)]) -> Vec<Fit> {
    let _ = (fw, settings);
    todo!("FitCheck.prefs")
}
pub fn environment(fw: &Firmware, switches: &[Vec<String>], also: &[(String, Vec<u8>)]) -> Fit {
    let _ = (fw, switches, also);
    todo!("FitCheck.environment")
}
pub fn web_proxy(fw: &Firmware) -> Fit {
    let _ = fw;
    todo!("FitCheck.webProxy")
}
pub fn boot_args(kernel: Option<&[u8]>, args: &str) -> Vec<Fit> {
    let _ = (kernel, args);
    todo!("FitCheck.bootArgs")
}
pub fn device_tree_property(kernel: Option<&[u8]>, node: &str, name: &str) -> Fit {
    let _ = (kernel, node, name);
    todo!("FitCheck.deviceTreeProperty")
}
pub fn sandbox_patch(kernel: Option<&[u8]>) -> Fit {
    let _ = kernel;
    todo!("FitCheck.sandboxPatch")
}
pub fn check_boot_args(log: &Log, kernel: Option<&[u8]>, args: &str) -> Result<()> {
    let _ = (log, kernel, args);
    todo!("FitCheck.checkBootArgs")
}
/// AppSync's pieces (the dylib `name` in `helpers`, its launcher on 2.x, the cache patch) checked into `log`.
pub fn check_app_sync(log: &Log, fw: &Firmware, helpers: &std::path::Path, name: &str) -> Result<()> {
    let _ = (log, fw, helpers, name);
    todo!("FitCheck.checkAppSync")
}
pub fn gles_front_end(fw: &Firmware, binary: &[u8], names: &str) -> Fit {
    let _ = (fw, binary, names);
    todo!("FitCheck.glesFrontEnd")
}
