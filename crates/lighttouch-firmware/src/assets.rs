//! mobileassetd's preinstall for 6.x, done at prepare time (SystemEdits/PreinstalledAssets.swift, its ODC cpio).
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::error::Result;
use crate::hfs::Editor;

/// The assets installed, or None when the volume has nothing to preinstall. `var_root`: see setup.rs.
pub fn seed_preinstalled_assets(
    data: &mut Editor,
    var_root: &str,
    major: u32,
    version: &str,
    build: &str,
    region: &str,
) -> Result<Option<Vec<String>>> {
    let _ = (data, var_root, major, version, build, region);
    todo!("SystemEdits.seedPreinstalledAssets")
}
