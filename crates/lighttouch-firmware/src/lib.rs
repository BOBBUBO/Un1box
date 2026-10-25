//! firmwarekit in Rust: prepares a device's storage from an IPSW, as the Swift FirmwareKit does, for every
//! platform the app runs on (Linux, Windows, macOS, Android). One module per FirmwareKit area:
//!
//!   entry      the catalog entry (keys, recipe)
//!   ipsw       IPSW members, Restore.plist, component paths
//!   img3       IMG3 / 8900 / IMG2 containers and AES
//!   lzss       kernelcache decompression
//!   vfdecrypt  the encrypted root filesystem image
//!   decrypt    the decrypt step and its cache
//!   udif       DMG images, the rootfs volume out of its partition map
//!   hfs        HFS+/HFSX volumes: read, edit and commit without a mount; grow; format
//!   page_nand  the page NAND image store (n72, s5l8900 boards)
//!   n72nand    the iPod touch 2G NAND writer;  n72nor  its NOR
//!   n45nand    the iPod touch 1G / iPhone NAND writer;  n45ftl  its FTL read back;  n45nor  its NOR
pub mod cache;
pub mod decrypt;
pub mod entry;
pub mod error;
pub mod hfs;
pub mod img3;
pub mod ipsw;
pub mod lzss;
pub mod n45ftl;
pub mod n45nand;
pub mod n45nor;
pub mod n72nand;
pub mod n72nor;
pub mod page_nand;
pub mod posio;
pub mod udif;
pub mod vfdecrypt;

pub use error::{Code, Error, Result};

/// The tool version recorded in locks and the decrypt cache identity.
pub const VERSION: &str = concat!("rust-", env!("CARGO_PKG_VERSION"));
