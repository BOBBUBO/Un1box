//! The jailbreak option: afc2 and Cydia with Substrate from the bootstrap (SystemEdits/Cydia.swift and
//! SystemEdits.installAFC2/afc2Copy).
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::error::Result;
use crate::hfs::Editor;
use std::path::{Path, PathBuf};

/// What install_jailbreak did: log lines, the paths to make root's and mobile's, and set-ID modes to restore.
pub struct Jailbreak {
    pub afc2: String,
    pub cydia: String,
    pub root: Vec<String>,
    pub mobile: Vec<String>,
    pub modes: Vec<(String, u16)>,
}

/// The bootstrap in `dir`, each file downloaded there first when missing or other bytes.
pub fn bootstrap(dir: &Path, log: &dyn Fn(&str)) -> Result<PathBuf> {
    let _ = (dir, log);
    todo!("Cydia.bootstrap")
}

/// afc2 and Cydia into the system volume (its data volume, if any, on private/var).
pub fn install_jailbreak(system: &mut Editor, bootstrap: &Path, typein_by_substrate: bool) -> Result<Jailbreak> {
    let _ = (system, bootstrap, typein_by_substrate);
    todo!("SystemEdits.installJailbreak")
}
