//! firmwarekit: the preparer. `create` is the command the apps run (JSON Lines on stdout, diagnostics on stderr);
//! the others are for development.
//!
//!   firmwarekit decrypt --catalog FILE --id ID --ipsw FILE --out DIR [--no-rootfs]
//!   firmwarekit extract-rootfs DMG OUT
//!   firmwarekit hfs-list IMG [PREFIX]          JSON Lines: owner, mode, flags, sizes, sha256, link per path
//!   firmwarekit hfs-edit IMG [EDIT...]         edits, then commit: write:PATH=HOSTFILE mkdirs:PATH
//!                                              symlink:PATH=TARGET rm:PATH mv:FROM=TO chmod:PATH=OCTAL
//!   firmwarekit hfs-grow IMG BYTES
//!   firmwarekit hfs-format IMG BYTES NAME [--journaled]
//!   firmwarekit write-n72-nand VOLUME OUT --blocks N --epoch E
//!   firmwarekit write-n45-nand VOLUME OUT (--fil-id 0xN | --iboot FILE) [--banks N] [--bbt-map]
//!   firmwarekit build-n72-nor --ipsw FILE --identity JSON --out FILE [--board B] [--types A,B] [--wrap all|A,B]
//!   firmwarekit build-n45-nor --ipsw FILE --identity JSON --out FILE [--board B] [--boot-args S]
use lighttouch_firmware::entry::Entry;
use lighttouch_firmware::hfs::{Editor, Volume};
use lighttouch_firmware::ipsw::Ipsw;
use std::path::PathBuf;
use std::process::ExitCode;

fn option(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

fn required(args: &[String], name: &str) -> Result<String, String> {
    option(args, name).ok_or_else(|| format!("{name} is required"))
}

fn decrypt(args: &[String]) -> Result<(), String> {
    let entry = Entry::load_from_catalog(&PathBuf::from(required(args, "--catalog")?), &required(args, "--id")?)
        .map_err(|e| e.to_string())?;
    let ipsw = Ipsw::open(&PathBuf::from(required(args, "--ipsw")?)).map_err(|e| e.to_string())?;
    let out = PathBuf::from(required(args, "--out")?);
    let rootfs = !args.iter().any(|a| a == "--no-rootfs");
    let started = std::time::Instant::now();
    let result = lighttouch_firmware::decrypt::decrypt(&ipsw, &entry, &out, rootfs, &|line| eprintln!("{line}"))
        .map_err(|e| e.to_string())?;
    for name in &result.files {
        let sha = lighttouch_firmware::cache::sha256_file(&out.join(name)).map_err(|e| e.to_string())?;
        println!("{sha}  {name}");
    }
    eprintln!("decrypted in {:.1} s (plain tail: {})", started.elapsed().as_secs_f64(), result.plain_tail);
    Ok(())
}

fn extract_rootfs(args: &[String]) -> Result<(), String> {
    let (Some(dmg), Some(out)) = (args.first(), args.get(1)) else { return Err("extract-rootfs DMG OUT".into()) };
    let started = std::time::Instant::now();
    lighttouch_firmware::udif::extract_rootfs(&PathBuf::from(dmg), &PathBuf::from(out)).map_err(|e| e.to_string())?;
    eprintln!("extracted in {:.1} s", started.elapsed().as_secs_f64());
    Ok(())
}

fn hfs_list(args: &[String]) -> Result<(), String> {
    use std::io::Write;
    let Some(image) = args.first() else { return Err("hfs-list IMG [PREFIX]".into()) };
    let volume = Volume::open(&PathBuf::from(image), false).map_err(|e| e.to_string())?;
    let mut out = std::io::BufWriter::new(std::io::stdout().lock());
    for e in volume.listing(args.get(1).map(String::as_str).unwrap_or(""), true).map_err(|e| e.to_string())? {
        writeln!(out, "{}", serde_json::to_string(&e).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn hfs_edit(args: &[String]) -> Result<(), String> {
    let Some(image) = args.first() else { return Err("hfs-edit IMG [EDIT...]".into()) };
    let started = std::time::Instant::now();
    let mut ed = Editor::open(&PathBuf::from(image)).map_err(|e| e.to_string())?;
    for edit in &args[1..] {
        let (verb, rest) = edit.split_once(':').ok_or_else(|| format!("not an edit: {edit}"))?;
        let pair = || rest.split_once('=').ok_or_else(|| format!("{verb} wants A=B: {edit}"));
        let result = match verb {
            "write" => pair().and_then(|(p, f)| ed.write_from(p, &PathBuf::from(f), None).map_err(|e| e.to_string())),
            "mkdirs" => ed.mkdirs(rest).map_err(|e| e.to_string()),
            "symlink" => pair().and_then(|(p, t)| ed.symlink(p, t).map_err(|e| e.to_string())),
            "rm" => ed.remove(rest).map_err(|e| e.to_string()),
            "mv" => pair().and_then(|(a, b)| ed.rename(a, b).map_err(|e| e.to_string())),
            "chmod" => pair().and_then(|(p, m)| {
                let mode = u16::from_str_radix(m, 8).map_err(|e| e.to_string())?;
                ed.chmod(p, mode).map_err(|e| e.to_string())
            }),
            _ => Err(format!("unknown edit: {verb}")),
        };
        result?;
    }
    ed.commit().map_err(|e| e.to_string())?;
    eprintln!("committed in {:.2} s", started.elapsed().as_secs_f64());
    Ok(())
}

fn hfs_grow(args: &[String]) -> Result<(), String> {
    let (Some(image), Some(bytes)) = (args.first(), args.get(1)) else { return Err("hfs-grow IMG BYTES".into()) };
    Editor::grow(&PathBuf::from(image), bytes.parse().map_err(|_| "BYTES: a number")?).map_err(|e| e.to_string())
}

fn hfs_format(args: &[String]) -> Result<(), String> {
    let (Some(image), Some(bytes), Some(name)) = (args.first(), args.get(1), args.get(2)) else {
        return Err("hfs-format IMG BYTES NAME [--journaled]".into());
    };
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    let bytes = bytes.parse().map_err(|_| "BYTES: a number")?;
    Editor::format(&PathBuf::from(image), bytes, name, args.iter().any(|a| a == "--journaled"), now).map_err(|e| e.to_string())
}

fn number(s: &str) -> Result<u32, String> {
    let r = match s.strip_prefix("0x") {
        Some(h) => u32::from_str_radix(h, 16),
        None => s.parse(),
    };
    r.map_err(|_| format!("not a number: {s}"))
}


fn write_n72_nand(args: &[String]) -> Result<(), String> {
    let (Some(volume), Some(out)) = (args.first(), args.get(1)) else {
        return Err("write-n72-nand VOLUME OUT --blocks N --epoch E".into());
    };
    let blocks = number(&required(args, "--blocks")?)? as usize;
    let epoch = number(&required(args, "--epoch")?)?;
    let started = std::time::Instant::now();
    let (written, meta) = lighttouch_firmware::n72nand::write(&PathBuf::from(volume), blocks, epoch, &PathBuf::from(out))
        .map_err(|e| e.to_string())?;
    eprintln!("{written} filesystem pages, {meta} metadata pages (epoch {epoch}) in {:.1} s", started.elapsed().as_secs_f64());
    Ok(())
}


fn write_n45_nand(args: &[String]) -> Result<(), String> {
    use lighttouch_firmware::n45nand;
    let (Some(volume), Some(out)) = (args.first(), args.get(1)) else {
        return Err("write-n45-nand VOLUME OUT (--fil-id 0xN | --iboot FILE) [--banks N] [--bbt-map]".into());
    };
    let fil = match (option(args, "--fil-id"), option(args, "--iboot")) {
        (Some(f), _) => number(&f)?,
        (None, Some(ib)) => {
            n45nand::fil_id(&std::fs::read(&ib).map_err(|e| format!("{ib}: {e}"))?).map_err(|e| e.to_string())?
        }
        _ => return Err("--fil-id or --iboot is required".into()),
    };
    let banks = option(args, "--banks").map(|b| number(&b)).transpose()?.unwrap_or(n45nand::BANKS as u32) as usize;
    let started = std::time::Instant::now();
    let (written, meta) =
        n45nand::write(&PathBuf::from(volume), &PathBuf::from(out), fil, banks, args.iter().any(|a| a == "--bbt-map"))
            .map_err(|e| e.to_string())?;
    eprintln!("{written} filesystem pages, {meta} metadata pages (NAND signature {fil:#x}) in {:.1} s", started.elapsed().as_secs_f64());
    Ok(())
}


fn identity_file(args: &[String]) -> Result<serde_json::Map<String, serde_json::Value>, String> {
    let path = required(args, "--identity")?;
    let text = std::fs::read(&path).map_err(|e| format!("{path}: {e}"))?;
    serde_json::from_slice(&text).map_err(|e| format!("{path}: {e}"))
}


fn build_n72_nor(args: &[String]) -> Result<(), String> {
    use lighttouch_firmware::n72nor;
    let ipsw = Ipsw::open(&PathBuf::from(required(args, "--ipsw")?)).map_err(|e| e.to_string())?;
    let board = option(args, "--board").unwrap_or_else(|| "n72ap".into());
    let prefix = format!("Firmware/all_flash/all_flash.{board}.production/");
    let mut images = std::collections::BTreeMap::new();
    for n in ipsw.names().map_err(|e| e.to_string())? {
        if n.starts_with(&prefix) && n.ends_with(".img3") {
            let d = ipsw.read(&n).map_err(|e| e.to_string())?;
            images.insert(n72nor::img3_type(&d).map_err(|e| e.to_string())?, d);
        }
    }
    // as N72Recipe: the stock order, of the types the manifest ships
    let manifest = String::from_utf8_lossy(&ipsw.read(&format!("{prefix}manifest")).map_err(|e| e.to_string())?).into_owned();
    let mut shipped = std::collections::BTreeSet::new();
    for m in manifest.split_whitespace() {
        let d = ipsw.read(&format!("{prefix}{m}")).map_err(|e| e.to_string())?;
        shipped.insert(n72nor::img3_type(&d).map_err(|e| e.to_string())?);
    }
    let types: Vec<String> = match option(args, "--types") {
        Some(t) => t.split(',').map(String::from).collect(),
        None => n72nor::ORDER.iter().filter(|t| shipped.contains(**t)).map(|t| t.to_string()).collect(),
    };
    let wrap: Option<Vec<String>> = option(args, "--wrap").filter(|w| w != "all").map(|w| w.split(',').map(String::from).collect());
    let nor = n72nor::build(&identity_file(args)?, &images, &types, wrap.as_deref()).map_err(|e| e.to_string())?;
    let out = required(args, "--out")?;
    std::fs::write(&out, nor).map_err(|e| format!("{out}: {e}"))?;
    eprintln!("types {types:?}");
    Ok(())
}


fn build_n45_nor(args: &[String]) -> Result<(), String> {
    use lighttouch_firmware::img3::{apple8900, img2};
    let ipsw = Ipsw::open(&PathBuf::from(required(args, "--ipsw")?)).map_err(|e| e.to_string())?;
    let board = option(args, "--board").unwrap_or_else(|| "n45ap".into());
    let prefix = format!("Firmware/all_flash/all_flash.{board}.production/");
    let mut images = std::collections::BTreeMap::new();
    for n in ipsw.names().map_err(|e| e.to_string())? {
        if n.starts_with(&prefix) && n.ends_with(".img2") {
            let body = apple8900::body(&ipsw.read(&n).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
            images.insert(img2::header(&body).map_err(|e| e.to_string())?.kind, body);
        }
    }
    let boot_args = option(args, "--boot-args").unwrap_or_else(|| lighttouch_firmware::n45nor::BOOT_ARGS.into());
    let nor = lighttouch_firmware::n45nor::build(&identity_file(args)?, &images, &boot_args).map_err(|e| e.to_string())?;
    let out = required(args, "--out")?;
    std::fs::write(&out, nor).map_err(|e| format!("{out}: {e}"))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("decrypt") => decrypt(&args[1..]),
        Some("extract-rootfs") => extract_rootfs(&args[1..]),
        Some("hfs-list") => hfs_list(&args[1..]),
        Some("hfs-edit") => hfs_edit(&args[1..]),
        Some("hfs-grow") => hfs_grow(&args[1..]),
        Some("hfs-format") => hfs_format(&args[1..]),
        Some("write-n72-nand") => write_n72_nand(&args[1..]),
        Some("write-n45-nand") => write_n45_nand(&args[1..]),
        Some("build-n72-nor") => build_n72_nor(&args[1..]),
        Some("build-n45-nor") => build_n45_nor(&args[1..]),
        _ => {
            eprintln!("usage: firmwarekit decrypt --catalog FILE --id ID --ipsw FILE --out DIR [--no-rootfs]");
            return ExitCode::from(64);
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("firmwarekit: {e}");
            ExitCode::from(1)
        }
    }
}
