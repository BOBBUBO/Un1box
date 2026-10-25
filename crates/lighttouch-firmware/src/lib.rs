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
//!   plistedit  property lists edited inside a volume, format kept
//!   fitcheck   proof that a guest piece fits the firmware (Mach-O, the shared cache);  shared_cache  dyld's cache
//!   activation lockdownd activated, ad hoc re-signing, the helpers' signature checks
//!   guest_package  the it_boot loader and the seed package
//!   keyboards, setup, assets, cydia  the data-volume seeds (keyboards, language, Setup finished, 6.x assets) and
//!              the jailbreak option
//!   page_nand  the page NAND image store (n72, s5l8900 boards)
//!   n72nand    the iPod touch 2G NAND writer;  n72nor  its NOR
//!   n45nand    the iPod touch 1G / iPhone NAND writer;  n45ftl  its FTL read back;  n45nor  its NOR
pub mod activation;
pub mod assets;
pub mod cache;
pub mod cydia;
pub mod decrypt;
pub mod entry;
pub mod error;
pub mod fitcheck;
pub mod guest_package;
pub mod hfs;
pub mod img3;
pub mod ipsw;
pub mod keyboards;
pub mod lzss;
pub mod n45ftl;
pub mod n45nand;
pub mod n45nor;
pub mod n72nand;
pub mod n72nor;
pub mod page_nand;
pub mod plistedit;
pub mod posio;
pub mod setup;
pub mod shared_cache;
pub mod udif;
pub mod vfdecrypt;

pub use error::{Code, Error, Result};

/// The tool version recorded in locks and the decrypt cache identity.
pub const VERSION: &str = concat!("rust-", env!("CARGO_PKG_VERSION"));
