//! Builds the C activation library (Packages/FirmwareKit/Sources/CActivation/activation.c) into this crate, as the
//! Swift package does with `-DLT_ACTIVATION_LIBRARY`. It is kept in C, not rewritten in Rust, so the one audited
//! pattern matcher is shared; it cross-compiles for Windows (x86_64-w64-mingw32-gcc-posix) and Android.

use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let src = manifest.join("../../Packages/FirmwareKit/Sources/CActivation");
    let c = src.join("activation.c");
    let include = src.join("include");

    println!("cargo:rerun-if-changed={}", c.display());
    println!("cargo:rerun-if-changed={}", include.join("CActivation.h").display());

    cc::Build::new()
        .file(&c)
        .include(&include)
        .define("LT_ACTIVATION_LIBRARY", None)
        .warnings(false)
        .compile("cactivation");
}
