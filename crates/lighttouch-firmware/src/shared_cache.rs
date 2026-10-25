//! The dyld shared cache: mappings, images, their symbols and C strings (SharedCache/SharedCache.swift), and
//! AppSync's cache patch.
//!
//! STUB: signatures fixed; bodies are being ported.
use crate::error::Result;

pub struct Image {
    pub path: String,
    pub address: u64,
}

pub struct DyldSharedCache {
    pub images: Vec<Image>,
}

impl DyldSharedCache {
    pub fn parse(data: Vec<u8>) -> Result<DyldSharedCache> {
        let _ = data;
        todo!("DyldSharedCache.init")
    }
    pub fn image(&self, path: &str) -> Option<&Image> {
        self.images.iter().find(|i| i.path == path)
    }
}
