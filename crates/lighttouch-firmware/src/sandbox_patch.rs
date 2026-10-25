//! SandboxPatch (SandboxPatch/SandboxPatch.swift): locating the Seatbelt kext's sb_evaluate in a decrypted,
//! decompressed kernelcache, so a jailbroken device can let Mobile Substrate's inserted library through a built-in
//! sandbox profile. FitCheck.sandboxPatch records the site as proof.
//!
//! sb_evaluate is found by pattern, not a fixed offset: the kext's "bad opcode" string (its report, inside
//! sb_evaluate) is located, its reference in the kext text walked for (the 4.x/5.x absolute literal-pool form and the
//! 6.x/7.x PC-relative form), and the enclosing function taken as the Thumb push{..,lr} prologue nearest before it.
//!
//! Ported here: `locate` and `Found`, which FitCheck uses. The real-iBoot kernelcache rewrap (complzss plus the
//! IMG3 around the patched kernel) belongs with the K48 recipe and is not ported in this module.
use crate::macho::le32;
use std::collections::HashMap;
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Found {
    /// The virtual address of sb_evaluate's prologue (the patch site).
    pub address: u32,
    /// The bytes there before the patch (a push{..,lr} prologue), for the record.
    pub original: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    NotMachO,
    NoSandboxKext,
    NoKextText,
    NoString,
    NoReference,
    NoPrologue,
    BadPatchSite,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Error::NotMachO => "not a 32-bit Mach-O kernelcache",
            Error::NoSandboxKext => "no com.apple.security.sandbox kext in the prelink info",
            Error::NoKextText => "the sandbox kext has no __text",
            Error::NoString => "the sandbox kext has no \"bad opcode\" string (sb_evaluate's report)",
            Error::NoReference => "no reference to the \"bad opcode\" string in the kext text",
            Error::NoPrologue => "no push{..,lr} prologue before the reference",
            Error::BadPatchSite => "the patch site is not a push{..,lr} prologue",
        })
    }
}

impl std::error::Error for Error {}

/// A real push{..,lr} prologue: 16-bit push with the LR bit, or push.w with the LR bit.
fn is_push_lr(b: &[u8]) -> bool {
    let hw = u16::from_le_bytes([b[0], b[1]]);
    (hw & 0xFE00 == 0xB400 && hw & 0x0100 != 0)
        || (hw == 0xE92D && u16::from_le_bytes([b[2], b[3]]) & 0x4000 != 0)
}

/// Locates sb_evaluate in a decrypted, decompressed kernelcache Mach-O.
pub fn locate(kernel: &[u8]) -> Result<Found, Error> {
    let img = Image::new(kernel)?;
    let klo = img.sandbox_kext_load_addr().ok_or(Error::NoSandboxKext)?;
    let (taddr, tsize) = img.kext_text(klo).ok_or(Error::NoKextText)?;
    let saddr = img.find_c_string("bad opcode").and_then(|o| img.off_to_addr(o)).ok_or(Error::NoString)?;
    let toff = img.addr_to_off(taddr).ok_or(Error::NoKextText)?;

    let mut prologues: Vec<u32> = Vec::new();
    let mut refs: Vec<u32> = Vec::new();
    let mut reg_word: HashMap<i32, u32> = HashMap::new(); // register -> last pc-relative word loaded
    let tsize = tsize as usize;
    let mut pos = 0usize;
    while pos + 1 < tsize {
        let at = toff + pos;
        if at + 2 > kernel.len() {
            break;
        }
        let addr = taddr.wrapping_add(pos as u32);
        let hw1 = img.u16(at);
        let len = if hw1 & 0xF800 >= 0xE800 { 4 } else { 2 };
        if len == 4 && (pos + 3 >= tsize || at + 4 > kernel.len()) {
            break;
        }
        let hw2 = if len == 4 { img.u16(at + 2) } else { 0 };

        if len == 2 && hw1 & 0xFE00 == 0xB400 && hw1 & 0x0100 != 0 {
            prologues.push(addr); // push {.., lr}
        } else if len == 4 && hw1 == 0xE92D && hw2 & 0x4000 != 0 {
            prologues.push(addr); // push.w {.., lr}
        } else if len == 2 && hw1 & 0xF800 == 0x4800 {
            // ldr rT, [pc, #imm]  (T1)
            let rt = ((hw1 >> 8) & 7) as i32;
            let base = addr.wrapping_add(4) & !3;
            let la = base.wrapping_add((hw1 & 0xFF) as u32 * 4);
            img.reference(la, saddr, rt, addr, &mut reg_word, &mut refs);
        } else if len == 4 && hw1 & 0xFF7F == 0xF85F {
            // ldr.w rT, [pc, #imm12]  (T2)
            let rt = ((hw2 >> 12) & 0xF) as i32;
            let base = addr.wrapping_add(4) & !3;
            let imm = (hw2 & 0xFFF) as u32;
            let la = if hw1 & 0x0080 != 0 { base.wrapping_add(imm) } else { base.wrapping_sub(imm) };
            img.reference(la, saddr, rt, addr, &mut reg_word, &mut refs);
        } else if len == 2 && hw1 & 0xFFF8 == 0x4478 {
            // add rD, pc  (Rm == pc): resolve a PC-relative delta loaded earlier
            let rd = (hw1 & 7) as i32;
            if let Some(&w) = reg_word.get(&rd) {
                if addr.wrapping_add(4).wrapping_add(w) == saddr {
                    refs.push(addr);
                }
            }
        }
        pos += len;
    }

    let x = *refs.iter().min().ok_or(Error::NoReference)?;
    let entry = prologues.iter().filter(|&&p| p <= x).max().copied().ok_or(Error::NoPrologue)?;
    let eo = img.addr_to_off(entry).ok_or(Error::NoPrologue)?;
    if eo + 4 > kernel.len() {
        return Err(Error::NoPrologue);
    }
    let orig = kernel[eo..eo + 4].to_vec();
    if !is_push_lr(&orig) {
        return Err(Error::BadPatchSite);
    }
    Ok(Found { address: entry, original: orig })
}

/// A read view over the kernelcache Mach-O: outer segments for address<->offset, the prelink info, and the kext
/// Mach-O's own __text.
struct Image<'a> {
    bytes: &'a [u8],
    // (vmaddr, vmsize, fileoff, filesize) per outer LC_SEGMENT.
    segs: Vec<(u32, u32, u32, u32)>,
    // (sectname, segname, addr, size, off) per outer section.
    sects: Vec<(String, String, u32, u32, u32)>,
}

fn cstr(b: &[u8], o: usize, n: usize) -> String {
    let end = (o..o + n).find(|&i| b.get(i) == Some(&0)).unwrap_or(o + n);
    String::from_utf8_lossy(&b[o..end.min(b.len())]).into_owned()
}

impl<'a> Image<'a> {
    fn new(b: &'a [u8]) -> Result<Image<'a>, Error> {
        if b.len() < 28 || le32(b, 0) != 0xFEED_FACE || le32(b, 4) != 12 {
            return Err(Error::NotMachO);
        }
        let mut segs = Vec::new();
        let mut sects = Vec::new();
        let mut off = 28usize;
        for _ in 0..le32(b, 16) {
            if off + 8 > b.len() {
                break;
            }
            let cmd = le32(b, off);
            let size = le32(b, off + 4) as usize;
            if size < 8 || off + size > b.len() {
                break;
            }
            if cmd == 1 {
                segs.push((le32(b, off + 24), le32(b, off + 28), le32(b, off + 32), le32(b, off + 36)));
                let nsects = le32(b, off + 48) as usize;
                let mut so = off + 56;
                for _ in 0..nsects {
                    if so + 68 > off + size {
                        break;
                    }
                    sects.push((cstr(b, so, 16), cstr(b, so + 16, 16), le32(b, so + 32), le32(b, so + 36), le32(b, so + 40)));
                    so += 68;
                }
            }
            off += size;
        }
        Ok(Image { bytes: b, segs, sects })
    }

    fn u16(&self, o: usize) -> u16 {
        u16::from_le_bytes([self.bytes[o], self.bytes[o + 1]])
    }
    fn u32(&self, o: usize) -> u32 {
        le32(self.bytes, o)
    }

    fn addr_to_off(&self, a: u32) -> Option<usize> {
        for &(vmaddr, vmsize, fileoff, filesize) in &self.segs {
            if a >= vmaddr && a < vmaddr.wrapping_add(vmsize) && a.wrapping_sub(vmaddr) < filesize {
                return Some(fileoff.wrapping_add(a.wrapping_sub(vmaddr)) as usize);
            }
        }
        None
    }
    fn off_to_addr(&self, o: usize) -> Option<u32> {
        for &(vmaddr, _, fileoff, filesize) in &self.segs {
            if o >= fileoff as usize && o < fileoff.wrapping_add(filesize) as usize {
                return Some(vmaddr.wrapping_add((o - fileoff as usize) as u32));
            }
        }
        None
    }

    fn find_c_string(&self, s: &str) -> Option<usize> {
        let mut needle = s.as_bytes().to_vec();
        needle.push(0);
        self.bytes.windows(needle.len()).position(|w| w == needle)
    }

    /// An absolute literal reference to saddr (4.x/5.x), or a delta to resolve at a later `add rD, pc` (6.x/7.x).
    fn reference(&self, la: u32, saddr: u32, rt: i32, at: u32, reg_word: &mut HashMap<i32, u32>, refs: &mut Vec<u32>) {
        let Some(lo) = self.addr_to_off(la) else { return };
        if lo + 4 > self.bytes.len() {
            return;
        }
        let w = self.u32(lo);
        if w == saddr {
            refs.push(at);
        } else {
            reg_word.insert(rt, w);
        }
    }

    /// The load address of the com.apple.security.sandbox kext: the _PrelinkExecutableLoadAddr in the same kext dict
    /// as the bundle id in __PRELINK_INFO. The key sits ahead of the id on 5.x-7.x but after it on 4.x, so take the
    /// occurrence nearest the id rather than the last one before it (which on 4.x is the prior kext's).
    fn sandbox_kext_load_addr(&self) -> Option<u32> {
        let (_, _, _, size, off) = self.sects.iter().find(|s| s.0 == "__info" && s.1 == "__PRELINK_INFO")?;
        let (off, size) = (*off as usize, *size as usize);
        let info = String::from_utf8_lossy(&self.bytes[off..(off + size).min(self.bytes.len())]).into_owned();
        let anchor = info.find("com.apple.security.sandbox")?;
        let key = "_PrelinkExecutableLoadAddr</key><integer";
        let mut before: Option<(usize, u32)> = None; // last occurrence before the id
        let mut after: Option<(usize, u32)> = None; // first occurrence at or after the id
        let mut search = 0usize;
        while let Some(rel) = info[search..].find(key) {
            let k = search + rel;
            let key_end = k + key.len();
            if let Some(gt_rel) = info[key_end..].find('>') {
                let gt = key_end + gt_rel + 1;
                if let Some(lt_rel) = info[gt..].find('<') {
                    let lt = gt + lt_rel;
                    let text = info[gt..lt].trim();
                    if let Some(hex) = text.strip_prefix("0x") {
                        if let Ok(v) = u32::from_str_radix(hex, 16) {
                            if k < anchor {
                                before = Some((k, v));
                            } else if after.is_none() {
                                after = Some((k, v));
                            }
                        }
                    }
                }
            }
            search = key_end;
        }
        match (before, after) {
            (None, a) => a.map(|x| x.1),
            (Some(b), None) => Some(b.1),
            (Some((bk, bv)), Some((ak, av))) => {
                // nearest the id wins; `before`'s distance is to the id, `after`'s is from the id
                let dist_before = anchor.saturating_sub(bk + key.len());
                let dist_after = ak.saturating_sub(anchor);
                Some(if dist_before <= dist_after { bv } else { av })
            }
        }
    }

    /// The kext Mach-O's own __text (addr, size) given its load address.
    fn kext_text(&self, kaddr: u32) -> Option<(u32, u32)> {
        let koff = self.addr_to_off(kaddr)?;
        if koff + 28 > self.bytes.len() || le32(self.bytes, koff) != 0xFEED_FACE {
            return None;
        }
        let mut off = koff + 28;
        for _ in 0..le32(self.bytes, koff + 16) {
            if off + 8 > self.bytes.len() {
                break;
            }
            let cmd = le32(self.bytes, off);
            let size = le32(self.bytes, off + 4) as usize;
            if size < 8 {
                break;
            }
            if cmd == 1 {
                let nsects = le32(self.bytes, off + 48) as usize;
                let mut so = off + 56;
                for _ in 0..nsects {
                    if so + 68 > off + size {
                        break;
                    }
                    if cstr(self.bytes, so, 16) == "__text" {
                        return Some((le32(self.bytes, so + 32), le32(self.bytes, so + 36)));
                    }
                    so += 68;
                }
            }
            off += size;
        }
        None
    }
}
