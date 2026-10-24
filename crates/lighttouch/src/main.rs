//! LightTouch desktop. One executable: run with no arguments for the app; `worker` runs one device session (started
//! by the app, one process per session because the engine starts once per process); the rest are developer tools.
mod audio;
mod cli;
mod worker;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.first().map(String::as_str) {
        Some("worker") => worker::main(&args[1..]),
        Some(command) if cli::handles(command) => cli::main(&args),
        _ => {
            eprintln!("the desktop UI is not built yet; try `lighttouch list`");
            2
        }
    };
    std::process::exit(code);
}
