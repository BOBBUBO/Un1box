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

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("decrypt") => decrypt(&args[1..]),
        Some("extract-rootfs") => extract_rootfs(&args[1..]),
        Some("hfs-list") => hfs_list(&args[1..]),
        Some("hfs-edit") => hfs_edit(&args[1..]),
        Some("hfs-grow") => hfs_grow(&args[1..]),
        Some("hfs-format") => hfs_format(&args[1..]),
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
