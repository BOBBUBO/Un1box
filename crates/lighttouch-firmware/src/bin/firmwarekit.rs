//! firmwarekit: the preparer. `create` is the command the apps run (JSON Lines on stdout, diagnostics on stderr);
//! the others are for development.
//!
//!   firmwarekit decrypt --catalog FILE --id ID --ipsw FILE --out DIR [--no-rootfs]
//!   firmwarekit extract-rootfs DMG OUT
use lighttouch_firmware::entry::Entry;
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

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("decrypt") => decrypt(&args[1..]),
        Some("extract-rootfs") => extract_rootfs(&args[1..]),
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
