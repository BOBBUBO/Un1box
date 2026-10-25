//! Developer hook for checking the UI where nothing can capture the screen (over ssh on macOS or Windows):
//! `LIGHTTOUCH_UI_SHOT=file.png` saves the window after `LIGHTTOUCH_UI_SHOT_AFTER` seconds (default 3), then closes
//! it. `LIGHTTOUCH_UI_PAGE` (settings, prepare, store) opens that page first; `LIGHTTOUCH_UI_START` starts the selected
//! device.
use super::{App, Page};
use eframe::egui;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub struct DevShot {
    path: PathBuf,
    at: Instant,
    requested: bool,
    saved: Arc<AtomicBool>,
}

impl DevShot {
    pub fn from_env() -> Option<Self> {
        let path = PathBuf::from(std::env::var_os("LIGHTTOUCH_UI_SHOT")?);
        let after = std::env::var("LIGHTTOUCH_UI_SHOT_AFTER").ok().and_then(|s| s.parse().ok()).unwrap_or(3.0);
        Some(Self {
            path,
            at: Instant::now() + Duration::from_secs_f64(after),
            requested: false,
            saved: Arc::new(AtomicBool::new(false)),
        })
    }
}

pub fn start_page() -> Option<Page> {
    match std::env::var("LIGHTTOUCH_UI_PAGE").ok()?.as_str() {
        "settings" => Some(Page::Settings),
        "licenses" => Some(Page::Licenses),
        "prepare" => Some(Page::Prepare),
        "store" => Some(Page::Store),
        _ => None,
    }
}

/// Start the selected device as the window opens, when asked to.
pub fn start(app: &mut App) {
    if std::env::var_os("LIGHTTOUCH_UI_START").is_some() {
        app.model.start(false);
    }
}

pub fn tick(app: &mut App, ctx: &egui::Context) {
    let Some(shot) = &mut app.dev_shot else { return };
    if shot.saved.load(Ordering::Acquire) {
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        return;
    }
    if !shot.requested && Instant::now() >= shot.at {
        shot.requested = true;
        let (path, saved, waker) = (shot.path.clone(), shot.saved.clone(), ctx.clone());
        ctx.request_screenshot(move |image| {
            let [w, h] = image.size;
            let rgba: Vec<u8> = image.pixels.iter().flat_map(|c| c.to_array()).collect();
            match image::save_buffer(&path, &rgba, w as u32, h as u32, image::ColorType::Rgba8) {
                Ok(()) => eprintln!("saved the window to {}", path.display()),
                Err(e) => eprintln!("couldn't save {}: {e}", path.display()),
            }
            saved.store(true, Ordering::Release);
            waker.request_repaint();
        });
    }
    ctx.request_repaint_after(Duration::from_millis(100));
}
