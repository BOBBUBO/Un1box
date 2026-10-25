//! Legacy Store (legacystore.app) client, ported from the Android app's LegacyStore.kt: search the archive of
//! classic iOS apps for one guest, list the archived versions that run on it, and download one copy after checking
//! it against the guest again. The MD5 checks archive integrity, not who published the app.
use crate::download::agent;
use crate::target::{Target, requires_newer, version};
use serde::Deserialize;
use serde_json::Value;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

const BASE: &str = "https://legacystore.app";
const JSON_LIMIT: u64 = 8 << 20;
const DOWNLOAD_LIMIT: u64 = 2 << 30;
/// Archived copies whose executable is cut short, each with an intact copy of the same version.
const BROKEN: &[(i64, i64)] = &[(59853, 179589)]; // Fruit Ninja 1.7.4: the search row's copy

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoreError {
    Cancelled,
    Failed(String),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Cancelled => f.write_str("Cancelled."),
            Self::Failed(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for StoreError {}

fn fail<T>(message: impl Into<String>) -> Result<T, StoreError> {
    Err(StoreError::Failed(message.into()))
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Compat {
    #[serde(default = "yes")]
    pub compatible: bool,
    #[serde(default)]
    pub reasons: Vec<String>,
}

fn yes() -> bool {
    true
}

/// One search result: an app, with the archived copy the catalog picked for the guest.
#[derive(Debug, Clone, Deserialize)]
pub struct StoreApp {
    pub ipa_id: i64,
    pub name: String,
    #[serde(default)]
    pub bundle_id: String,
    pub developer: Option<String>,
    pub version: Option<String>,
    pub min_os: Option<String>,
    pub size: Option<u64>,
    pub icon_url: Option<String>,
    pub download_url: String,
    pub app_url: Option<String>,
    pub compat: Option<Compat>,
}

impl StoreApp {
    /// Why it can't be installed on the guest, in plain words, or `None`.
    pub fn incompatibility(&self) -> Option<String> {
        let compat = self.compat.as_ref()?;
        if compat.compatible {
            return None;
        }
        let reason = compat.reasons.first().map(String::as_str).unwrap_or("");
        let text = if reason == "armv6_slice_contains_armv7_code"
            || (reason.starts_with("no_") && reason.ends_with("_slice"))
        {
            "Needs a newer processor"
        } else if let Some(v) = reason.strip_prefix("requires_ios_") {
            return Some(format!("Requires iOS {v}"));
        } else if reason.starts_with("capability:!")
            || reason.ends_with("device_family")
            || reason == "family_not_requested"
        {
            "Not made for this device"
        } else if reason.starts_with("capability:") {
            "Needs hardware this device doesn't have"
        } else {
            match reason {
                "encrypted" => "Encrypted, so it can't run in LightTouch",
                "unavailable" => "Download no longer available",
                "not_analyzed" => "Not checked for compatibility yet",
                _ => "Not compatible with this device",
            }
        };
        Some(text.to_string())
    }

    /// The same app with another archived copy chosen.
    pub fn with_copy(&self, copy: &StoreCopy) -> Self {
        Self { ipa_id: copy.ipa_id, version: Some(copy.version.clone()), size: copy.size, ..self.clone() }
    }
}

/// The copy to select first for a search row that names `ipa_id`.
pub fn preferred(ipa_id: i64) -> i64 {
    BROKEN.iter().find(|(bad, _)| *bad == ipa_id).map(|(_, good)| *good).unwrap_or(ipa_id)
}

/// One archived copy of one version, as the version picker lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreCopy {
    pub ipa_id: i64,
    pub version: String,
    pub size: Option<u64>,
    pub minimum_os: Option<String>,
}

fn encode(text: &str) -> String {
    let mut out = String::new();
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn get(url: &str) -> Result<ureq::http::Response<ureq::Body>, StoreError> {
    if !url.starts_with("https://") || url[8..].split('/').next().is_none_or(|h| h.is_empty() || h.contains('@')) {
        return fail("The catalog supplied an invalid HTTPS address.");
    }
    let response = agent(Duration::from_secs(30))
        .get(url)
        .call()
        .map_err(|e| StoreError::Failed(format!("Couldn't reach Legacy Store. Check your connection. ({e})")))?;
    let status = response.status().as_u16();
    if status != 200 {
        let body = response.into_body().with_config().limit(4096).read_to_string().unwrap_or_default();
        return fail(match status {
            400 if body.contains("device must be one of") => "Legacy Store doesn't support this device yet.".into(),
            503 => "The Internet Archive is busy; try again later.".into(),
            _ => format!("Legacy Store couldn't answer the request (HTTP {status})."),
        });
    }
    Ok(response)
}

fn json(path: &str, query: &[(&str, &str)]) -> Result<Value, StoreError> {
    let mut url = format!("{BASE}{path}");
    for (i, (key, value)) in query.iter().enumerate() {
        url.push(if i == 0 { '?' } else { '&' });
        url.push_str(&format!("{}={}", encode(key), encode(value)));
    }
    let text = get(&url)?
        .into_body()
        .with_config()
        .limit(JSON_LIMIT)
        .read_to_string()
        .map_err(|_| StoreError::Failed("Legacy Store sent a response that's too large or unreadable.".into()))?;
    serde_json::from_str(&text).map_err(|_| StoreError::Failed("Legacy Store sent an unreadable response.".into()))
}

fn apps(query: &[(&str, &str)], target: &Target) -> Result<Vec<StoreApp>, StoreError> {
    let mut query = query.to_vec();
    query.push(("device", &target.product_type));
    query.push(("os", &target.ios));
    let value = json("/api/emulator/apps", &query)?;
    let list = value.get("apps").and_then(Value::as_array).filter(|l| l.len() <= 500);
    let Some(list) = list else { return fail("Legacy Store sent an unreadable app list.") };
    list.iter()
        .map(|entry| {
            serde_json::from_value::<StoreApp>(entry.clone())
                .ok()
                .filter(|a| a.ipa_id > 0 && !a.name.is_empty() && !a.download_url.is_empty())
                .ok_or_else(|| StoreError::Failed("Legacy Store sent an unreadable app entry.".into()))
        })
        .collect()
}

/// An empty query lists featured apps that run on the guest; a query also includes ones that don't.
pub fn search(query: &str, target: &Target) -> Result<Vec<StoreApp>, StoreError> {
    let query = query.trim();
    if query.is_empty() {
        apps(&[("limit", "50")], target)
    } else {
        apps(&[("limit", "50"), ("q", query), ("incompatible", "include")], target)
    }
}

/// The catalog's current row for one copy, which must still be compatible.
pub fn compatible_copy(ipa_id: i64, target: &Target) -> Result<StoreApp, StoreError> {
    let id = ipa_id.to_string();
    let found = apps(&[("ipa_id", &id)], target)?;
    let [app] = found.as_slice() else { return fail("This copy is no longer available.") };
    if app.ipa_id != ipa_id {
        return fail("This copy is no longer available.");
    }
    if let Some(problem) = app.incompatibility() {
        return fail(problem);
    }
    Ok(app.clone())
}

fn architectures_run(list: Option<&Value>, target: &Target) -> bool {
    list.and_then(Value::as_array).is_some_and(|a| a.iter().filter_map(Value::as_str).any(|arch| target.runs(arch)))
}

fn text(value: &Value, key: &str) -> Option<String> {
    match value.get(key)? {
        Value::String(s) if !s.is_empty() && s != "null" => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

/// Every archived copy of the app that could run on the guest, newest first. The copy the search row named is
/// always kept so the picker can show it selected, unless it is broken.
pub fn versions(app: &StoreApp, target: &Target) -> Result<Vec<StoreCopy>, StoreError> {
    let key = if !app.bundle_id.is_empty() {
        app.bundle_id.clone()
    } else {
        app.app_url.as_deref().unwrap_or("").trim_end_matches('/').rsplit('/').next().unwrap_or("").to_string()
    };
    if key.is_empty() {
        return fail("This app has no catalog identifier.");
    }
    let value = json(&format!("/api/v1/apps/{}/versions", encode(&key)), &[])?;
    let Some(data) = value.get("data").and_then(Value::as_array) else {
        return fail("Legacy Store sent unreadable version information.");
    };
    let mut rows = Vec::new();
    for entry in data {
        let minimum = text(entry, "minimum_os_version");
        for copy in entry.get("copies").and_then(Value::as_array).into_iter().flatten() {
            let Some(id) = text(copy, "ipa_id").and_then(|s| s.parse::<i64>().ok()) else { continue };
            if BROKEN.iter().any(|(bad, _)| *bad == id) {
                continue;
            }
            let macho_minimum = text(copy, "macho_min_os");
            let fits = copy.get("install_status").and_then(Value::as_str) == Some("installable")
                && architectures_run(copy.get("architectures"), target)
                && [&minimum, &macho_minimum].into_iter().flatten().all(|m| !requires_newer(m, target));
            if fits || id == app.ipa_id {
                let minimum_os = [&minimum, &macho_minimum]
                    .into_iter()
                    .flatten()
                    .max_by_key(|m| version(m).unwrap_or([0, 0, 0]))
                    .cloned();
                rows.push(StoreCopy {
                    ipa_id: id,
                    version: text(entry, "version").unwrap_or_else(|| "Unknown".into()),
                    size: copy.get("size").and_then(Value::as_u64).filter(|s| *s > 0),
                    minimum_os,
                });
            }
        }
    }
    Ok(rows)
}

/// Identity, availability, processor, device family and minimum iOS of a copy's details.
fn check_copy(details: &Value, app: &StoreApp, target: &Target) -> Result<(), StoreError> {
    if text(details, "ipa_id") != Some(app.ipa_id.to_string())
        || text(details, "bundle_id").unwrap_or_default() != app.bundle_id
    {
        return fail("The archived copy no longer matches this app.");
    }
    if details.get("available").and_then(Value::as_bool) != Some(true) {
        return fail("This archived download is no longer available.");
    }
    let Some(binary) = details.get("binary").filter(|b| b.is_object()) else {
        return fail("This copy hasn't been checked for compatibility.");
    };
    if binary.get("install_status").and_then(Value::as_str) != Some("installable") {
        return fail("This copy is encrypted or hasn't been classified as installable.");
    }
    if !architectures_run(binary.get("architectures"), target)
        || (target.arch == "armv6" && binary.get("armv7_code").and_then(Value::as_bool) == Some(true))
    {
        return fail("This copy needs a newer processor than this device has.");
    }
    if let Some(families) = binary.get("device_family_macho").and_then(Value::as_array) {
        let families: Vec<String> =
            families.iter().map(|f| f.as_str().map(str::to_string).unwrap_or_else(|| f.to_string())).collect();
        if !families.is_empty() && !families.contains(&target.family) {
            return fail("This copy isn't made for this device.");
        }
    }
    for minimum in [app.min_os.clone(), text(binary, "macho_min_os")].into_iter().flatten() {
        if requires_newer(&minimum, target) {
            return fail(format!("Requires iOS {minimum}; this device runs iOS {}.", target.ios));
        }
    }
    Ok(())
}

/// Download one revalidated copy into a new folder under `scratch`, returning the IPA. The caller deletes the
/// folder after installing. `progress(done, total)` returns false to cancel.
pub fn download(
    app: &StoreApp,
    target: &Target,
    scratch: &Path,
    progress: &mut dyn FnMut(u64, Option<u64>) -> bool,
) -> Result<PathBuf, StoreError> {
    let current = compatible_copy(app.ipa_id, target)?;
    if current.bundle_id != app.bundle_id {
        return fail("The archived copy no longer matches this app.");
    }
    let details = json(&format!("/api/v1/copies/{}", current.ipa_id), &[])?;
    check_copy(&details, &current, target)?;
    let checksum = text(&details, "md5");
    if checksum.as_ref().is_some_and(|c| c.len() != 32 || !c.bytes().all(|b| b.is_ascii_hexdigit())) {
        return fail("The archive supplied an invalid file checksum.");
    }
    let size = details.get("size").and_then(Value::as_u64);
    if size.is_some_and(|s| s == 0 || s > DOWNLOAD_LIMIT) {
        return fail("The archive supplied an invalid file size.");
    }
    let dir = scratch.join(format!("store-{}-{}", current.ipa_id, std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).map_err(|e| StoreError::Failed(format!("Couldn't create a download folder: {e}")))?;
    let result = (|| {
        let partial = dir.join("download.partial");
        let response = get(&current.download_url)?;
        let mut body = response.into_body().into_reader();
        let mut out = std::fs::File::create(&partial).map_err(|e| StoreError::Failed(e.to_string()))?;
        let mut digest = md5::Context::new();
        let mut buffer = vec![0u8; 1 << 16];
        let mut count = 0u64;
        loop {
            let read = body.read(&mut buffer).map_err(|e| StoreError::Failed(format!("Download failed: {e}")))?;
            if read == 0 {
                break;
            }
            count += read as u64;
            if count > DOWNLOAD_LIMIT || size.is_some_and(|s| count > s) {
                return fail("The download is larger than the archive says it is.");
            }
            out.write_all(&buffer[..read]).map_err(|e| StoreError::Failed(e.to_string()))?;
            digest.consume(&buffer[..read]);
            if !progress(count, size) {
                return Err(StoreError::Cancelled);
            }
        }
        out.sync_all().map_err(|e| StoreError::Failed(e.to_string()))?;
        if count == 0 || size.is_some_and(|s| count != s) {
            return fail("The download is incomplete or differs from the archive's size.");
        }
        if let Some(checksum) = &checksum {
            if format!("{:x}", digest.finalize()) != checksum.to_lowercase() {
                return fail("The downloaded IPA failed its checksum check.");
            }
        }
        let ipa = dir.join("App.ipa");
        std::fs::rename(&partial, &ipa).map_err(|e| StoreError::Failed(e.to_string()))?;
        Ok(ipa)
    })();
    if result.is_err() {
        let _ = std::fs::remove_dir_all(&dir);
    }
    result
}

/// An app icon's bytes (PNG), or `None` if it can't be fetched.
pub fn icon(url: &str) -> Option<Vec<u8>> {
    let mut bytes = Vec::new();
    get(url).ok()?.into_body().into_reader().take(1 << 20).read_to_end(&mut bytes).ok()?;
    Some(bytes)
}
