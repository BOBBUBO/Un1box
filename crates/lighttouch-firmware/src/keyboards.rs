//! The keyboards a firmware has (its TextInput bundles' input modes and, from 5.x, each language's defaults) and the
//! AppleKeyboards a prepared device gets for its language (SystemEdits/Keyboards.swift). Off the Mac there are no
//! Text Input Sources: the device gets its language's keyboards and those `create --keyboards` names, as layouts.
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::hfs::Editor;

pub struct KeyboardCatalog {
    pub modes: std::collections::BTreeMap<String, plist::Dictionary>,
    pub defaults: std::collections::BTreeMap<String, Vec<String>>,
}

impl KeyboardCatalog {
    /// The catalog of the volume's System/Library/TextInput; None where it has none (1.x).
    pub fn read(system: &Editor) -> Option<KeyboardCatalog> {
        let _ = system;
        todo!("KeyboardCatalog.init")
    }
    /// AppleKeyboards for `language` and `locale`, with `extra` (create --keyboards: Mac input source IDs).
    pub fn keyboards(&self, language: &str, locale: &str, extra: &[String]) -> Vec<String> {
        let _ = (language, locale, extra);
        todo!("KeyboardCatalog.keyboards")
    }
}
