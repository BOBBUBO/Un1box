//! Activation: lockdownd activated in place (SystemEdits/Activation.swift over Sources/CActivation/activation.c,
//! built here by build.rs), the iOS 6 data-ark route, the ad hoc re-signing, and the Mach-O signature checks of the
//! guest helpers (MachOSignature).
//!
//! STUB: signatures fixed for SystemEdits; bodies are being ported.
use crate::error::Result;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Patch {
    pub strategy: String,
    pub isa: String,
    pub offset: usize,
    pub original: Vec<u8>,
    pub replacement: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActivationResult {
    pub input_sha256: String,
    pub output_sha256: String,
    pub patch: Option<Patch>,
    /// The data route (iOS 6): lockdownd left stock, these keys seeded into /var/root/Library/Lockdown/data_ark.plist.
    pub data_ark: Option<Vec<(String, String)>>,
}

impl ActivationResult {
    /// The lock's "activation" object (input_sha256, output_sha256, [data_ark], [patch]).
    pub fn record(&self) -> serde_json::Value {
        todo!("Activation.Result.record")
    }
}

/// An ActivationFailure: no binary strategy matched (the caller may try `data_ark_route`).
#[derive(Debug, Clone)]
pub struct Failure(pub String);

/// lockdownd's bytes activated: (the new bytes, what was done).
pub fn run(lockdownd: &[u8]) -> std::result::Result<(Vec<u8>, ActivationResult), Failure> {
    let _ = lockdownd;
    todo!("Activation.run")
}

/// iOS 6's lockdownd, which no binary strategy matches: its data ark says FactoryActivated.
pub fn data_ark_route(lockdownd: &[u8]) -> Option<ActivationResult> {
    let _ = lockdownd;
    todo!("Activation.dataArkRoute")
}

/// The existing code-page hashes rebuilt as an ad hoc signature, entitlements and special slots kept.
pub fn signed(data: &[u8]) -> Result<Vec<u8>> {
    let _ = data;
    todo!("Activation.signed")
}

/// Why a guest tool (it_pbd, it_seal, ...) can't be baked, if it can't (MachOSignature.guestToolProblem).
pub fn guest_tool_problem(bin: &[u8]) -> Option<String> {
    let _ = bin;
    todo!("MachOSignature.guestToolProblem")
}
pub fn app_sync_problem(bin: &[u8]) -> Option<String> {
    let _ = bin;
    todo!("MachOSignature.appSyncProblem")
}
pub fn early_arm_problem(bin: &[u8]) -> Option<String> {
    let _ = bin;
    todo!("MachOSignature.earlyARMProblem")
}
