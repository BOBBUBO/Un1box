//! The device's language and region, and Setup Assistant's finished state, seeded into the data volume
//! (SystemEdits/FinishedSetup.swift, Portability/PreferredLocalization.swift). `var_root` is the folder that is
//! /private/var on the device: "" in a data volume's editor, "private/var" in a single-volume system editor.
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::error::Result;
use crate::hfs::Editor;
use crate::keyboards::KeyboardCatalog;
use std::collections::HashSet;

/// What seed_language chose.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Language {
    pub language: String,
    pub locale: String,
    pub keyboards: Vec<String>,
}

/// The region formats the firmware offers (the locales in its ICU data); None where it has none.
pub fn region_formats(system: &Editor) -> Option<HashSet<String>> {
    let _ = system;
    todo!("SystemEdits.regionFormats")
}

/// The language (BCP 47 `tag`, the device's region with it) first in each user's .GlobalPreferences that lists
/// the firmware's languages, AppleLocale set, AppleKeyboards from `catalog`. None where the firmware has no list.
pub fn seed_language(
    data: &mut Editor,
    var_root: &str,
    tag: Option<&str>,
    keyboards: Option<&[String]>,
    catalog: Option<&KeyboardCatalog>,
    formats: Option<&HashSet<String>>,
) -> Result<Option<Language>> {
    let _ = (data, var_root, tag, keyboards, catalog, formats);
    todo!("SystemEdits.seedLanguage")
}

/// Setup Assistant's finished state for iOS `major` (5, 6, 7) in `language`/`locale`, dated `now` (Unix seconds).
pub fn seed_finished_setup(data: &mut Editor, var_root: &str, major: u32, language: &str, locale: &str, now: i64) -> Result<()> {
    let _ = (data, var_root, major, language, locale, now);
    todo!("SystemEdits.seedFinishedSetup")
}
