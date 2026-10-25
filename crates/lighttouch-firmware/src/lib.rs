//! firmwarekit in Rust: prepares a device's storage from an IPSW, as the Swift FirmwareKit does, for every
//! platform the app runs on (Linux, Windows, macOS, Android). One module per FirmwareKit area:
//!
//!   entry      the catalog entry (keys, recipe)
//!   ipsw       IPSW members, Restore.plist, component paths
//!   img3       IMG3 / 8900 / IMG2 containers and AES
//!   lzss       kernelcache decompression
//!   vfdecrypt  the encrypted root filesystem image
//!   decrypt    the decrypt step and its cache
pub mod cache;
pub mod decrypt;
pub mod entry;
pub mod error;
pub mod img3;
pub mod ipsw;
pub mod lzss;
pub mod vfdecrypt;

pub use error::{Code, Error, Result};

/// The tool version recorded in locks and the decrypt cache identity.
pub const VERSION: &str = concat!("rust-", env!("CARGO_PKG_VERSION"));
