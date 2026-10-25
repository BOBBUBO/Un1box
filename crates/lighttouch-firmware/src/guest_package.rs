//! The guest package: the it_boot loader and the arch's seed package from <arch>.itpack baked into the system
//! volume (GuestPackage/GuestPackage.swift, FirmwareSchema/GuestArchive.swift), and the hook backups.
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::error::Result;
use crate::fitcheck;
use crate::hfs::Editor;
use std::collections::HashSet;
use std::path::Path;

/// What was baked: device.lock.json's guest_package.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub family: String,
    pub seed: i64,
    pub version: String,
    pub gles: bool,
    pub itpack_path: String,
    pub itpack_sha256: String,
    pub hooks: Vec<String>,
    pub jobs: Vec<String>,
}

impl Record {
    pub fn object(&self) -> serde_json::Value {
        serde_json::json!({
            "family": self.family, "seed": self.seed, "version": self.version, "gles": self.gles,
            "itpack": {"path": self.itpack_path, "sha256": self.itpack_sha256},
            "hooks": self.hooks, "jobs": self.jobs,
        })
    }
}

/// Preserves `target`'s original (or its absence) before an installer changes it; returns the path the caller
/// makes root's.
pub fn preserve_hook(volume: &mut Editor, target: &str) -> Result<String> {
    let _ = (volume, target);
    todo!("GuestPackage.preserveHook")
}

/// The loader and the seed package of `itpack` into `volume`: (the paths written, which the caller makes root's;
/// the record).
pub fn seed(
    volume: &mut Editor,
    itpack: &Path,
    gles: bool,
    omitted: &HashSet<String>,
    fit: &fitcheck::Log,
) -> Result<(Vec<String>, Record)> {
    let _ = (volume, itpack, gles, omitted, fit);
    todo!("GuestPackage.seed")
}
