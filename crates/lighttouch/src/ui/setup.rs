//! Adding a device: a welcome on first run, then the choice between preparing one from an IPSW and adding a
//! prepared image the user has, progress while it is copied in, and "ready".
use super::{App, Page, icons, theme, widgets};
use eframe::egui::{self, RichText, Ui};
use lighttouch_catalog::Bootrom;
use lighttouch_model::{AddRequest, FoundAssets, Model};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Welcome,
    Files,
    Creating,
    Done,
}

#[derive(Default)]
pub struct SetupState {
    pub system: Option<PathBuf>,
    pub bootrom: Option<PathBuf>,
    pub name: String,
    pub found: Option<String>,
    pub error: Option<String>,
    pub added: Option<String>,
    /// (stage, seconds) of the preparation that made it, for "Details".
    pub timings: Vec<(String, f64)>,
}

fn centered(ui: &mut Ui, width: f32, add: impl FnOnce(&mut Ui)) {
    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        let w = ui.available_width().min(width);
        let margin = ((ui.available_width() - w) / 2.0).max(24.0);
        egui::Frame::new()
            .inner_margin(egui::Margin { left: margin as i8, right: margin as i8, top: 36, bottom: 24 })
            .show(ui, |ui| {
                ui.set_max_width(w);
                add(ui);
            });
    });
}

pub fn show(app: &mut App, ui: &mut Ui, step: Step) {
    let p = app.palette;
    let can_cancel = !app.model.devices.is_empty();
    match step {
        Step::Welcome => centered(ui, 520.0, |ui| {
            ui.add_space(30.0);
            let (rect, _) = ui.allocate_exact_size(egui::vec2(64.0, 64.0), egui::Sense::hover());
            ui.painter().rect_filled(rect, egui::CornerRadius::same(18), p.primary);
            ui.painter().text(
                rect.center(),
                egui::Align2::CENTER_CENTER,
                icons::PHONE,
                egui::FontId::new(36.0, theme::icons_fill()),
                p.on_primary,
            );
            ui.add_space(16.0);
            ui.label(RichText::new("Welcome to LightTouch").font(theme::headline()));
            ui.add_space(6.0);
            ui.label(
                widgets::muted(&p, "Classic iPod touch and iPhone software, running on your computer.").size(15.0),
            );
            ui.add_space(22.0);
            ui.label(RichText::new("To start you need an iOS version for it to run. LightTouch can download one from Apple's archive and prepare it for you, or you can add a device image you already have."));
            ui.add_space(22.0);
            if widgets::primary(ui, &p, "Get started").clicked() {
                app.page = Page::Setup(Step::Files);
            }
        }),
        Step::Files => centered(ui, 600.0, |ui| {
            ui.horizontal(|ui| {
                if can_cancel && widgets::icon_button(ui, &p, icons::BACK, "Back").clicked() {
                    app.page = Page::Device;
                }
                ui.label(RichText::new("Add a device").font(theme::headline()));
            });
            ui.add_space(14.0);
            if widgets::action_card(
                ui,
                &p,
                icons::DOWNLOAD,
                "Prepare from an IPSW",
                "Pick a device and iOS version; LightTouch downloads and sets it up",
                true,
            )
            .clicked()
            {
                if app.prepare.step == super::prepare::Step::Progress && app.model.busy().is_none() {
                    app.prepare = Default::default();
                }
                app.page = Page::Prepare;
            }
            ui.add_space(22.0);
            ui.label(RichText::new("Or add a prepared device image").font(theme::title()));
            ui.add_space(8.0);
            files_card(app, ui);
        }),
        Step::Creating => centered(ui, 520.0, |ui| {
            ui.add_space(40.0);
            ui.label(RichText::new("Creating your device").font(theme::headline()));
            ui.add_space(16.0);
            let progress = app.model.busy().map(|(_, progress)| progress).unwrap_or_default();
            let percent = progress.fraction().map(|f| format!(" · {:.0}%", f * 100.0)).unwrap_or_default();
            ui.label(
                RichText::new(format!(
                    "{}{percent}",
                    if progress.phase.is_empty() { "Starting" } else { &progress.phase }
                ))
                .size(15.0),
            );
            ui.add_space(10.0);
            widgets::progress(ui, &p, progress.fraction(), ui.available_width().min(420.0));
            ui.add_space(14.0);
            ui.label(widgets::muted(&p, "Keep LightTouch open until it finishes."));
            ui.add_space(10.0);
            if widgets::text_button(ui, &p, "Cancel").clicked() {
                app.model.cancel_task();
            }
        }),
        Step::Done => centered(ui, 520.0, |ui| {
            ui.add_space(40.0);
            ui.label(widgets::icon_text(icons::CHECK_CIRCLE, 54.0).color(p.primary));
            ui.add_space(10.0);
            let name = app.setup.added.clone().unwrap_or_else(|| "Your device".into());
            ui.label(RichText::new(format!("{name} is ready")).font(theme::headline()));
            ui.add_space(6.0);
            ui.label(widgets::muted(&p, "The first start takes longer while iOS sets itself up."));
            if !app.setup.timings.is_empty() {
                ui.add_space(12.0);
                egui::CollapsingHeader::new(widgets::muted(&p, "Details")).id_salt("prepare-details").show(ui, |ui| {
                    ui.spacing_mut().interact_size.y = 20.0;
                    egui::Grid::new("timings").num_columns(2).spacing([28.0, 2.0]).show(ui, |ui| {
                        for (stage, secs) in &app.setup.timings {
                            ui.label(widgets::muted(&p, stage.as_str()));
                            ui.label(format!("{secs:.1} s"));
                            ui.end_row();
                        }
                    });
                });
            }
            ui.add_space(22.0);
            ui.horizontal(|ui| {
                let can_start = !app.model.session_active();
                if can_start && widgets::primary(ui, &p, "Start it now").clicked() {
                    app.model.start(false);
                    app.page = Page::Device;
                }
                if widgets::text_button(ui, &p, "Not now").clicked() {
                    app.page = Page::Device;
                }
            });
        }),
    }
}

fn files_card(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    widgets::card(ui, &p, |ui| {
        let found_label =
            app.setup.found.clone().unwrap_or_else(|| "LightTouch finds the system and boot file in it".into());
        widgets::setting_row(ui, &p, icons::FOLDER, "Choose a folder", &found_label, |ui| {
            if widgets::tonal(ui, &p, "Choose…").clicked() {
                if let Some(folder) =
                    rfd::FileDialog::new().set_title("Choose a folder with a device image").pick_folder()
                {
                    let found: FoundAssets = Model::scan_folder(&folder);
                    app.setup.found = Some(found.describe(&folder));
                    if found.system.is_some() {
                        app.setup.system = found.system;
                    }
                    if found.bootrom.is_some() {
                        app.setup.bootrom = found.bootrom;
                    }
                }
            }
        });
        let system_label = app
            .setup
            .system
            .as_ref()
            .map(|p| file_name(p))
            .unwrap_or_else(|| "Required · .itbase or a prepared folder".into());
        widgets::setting_row(ui, &p, icons::PACKAGE, "iOS system", &system_label, |ui| {
            if widgets::tonal(ui, &p, "Choose…").clicked() {
                if let Some(file) = rfd::FileDialog::new()
                    .set_title("Choose an iOS system")
                    .add_filter("Device image", &["itbase"])
                    .pick_file()
                {
                    app.setup.system = Some(file);
                }
            }
        });
        let board = app.setup.system.as_deref().and_then(board_of);
        let needs_rom = board.as_deref().is_none_or(|b| Bootrom::for_board(b).is_some());
        if needs_rom {
            let rom_label = app
                .setup
                .bootrom
                .as_ref()
                .map(|p| file_name(p))
                .unwrap_or_else(|| "The iPod touch 2G and first models need one".into());
            widgets::setting_row(ui, &p, icons::CPU, "Boot file", &rom_label, |ui| {
                if widgets::tonal(ui, &p, "Choose…").clicked() {
                    if let Some(file) = rfd::FileDialog::new().set_title("Choose the boot ROM").pick_file() {
                        app.setup.bootrom = Some(file);
                    }
                }
            });
        }
        if app.setup.system.is_some() && needs_rom && board.is_some() && app.setup.bootrom.is_none() {
            ui.label(
                RichText::new("Without the boot file this device will not start; you can still add it now.")
                    .color(theme::WARNING)
                    .size(13.0),
            );
        }
        ui.add_space(8.0);
        ui.label(RichText::new("Name (optional)").size(13.0).color(p.on_surface_variant));
        ui.add(
            egui::TextEdit::singleline(&mut app.setup.name)
                .hint_text("Named after the model and iOS version")
                .desired_width(f32::INFINITY)
                .margin(egui::vec2(10.0, 8.0)),
        );
        if let Some(error) = &app.setup.error {
            ui.add_space(6.0);
            ui.label(RichText::new(error).color(p.error));
        }
        ui.add_space(12.0);
        let ready = app.setup.system.is_some() && !app.model.session_active();
        if widgets::primary_enabled(ui, &p, "Create device", ready).clicked() {
            let request = AddRequest {
                system: app.setup.system.clone().unwrap(),
                bootrom: app.setup.bootrom.clone(),
                name: Some(app.setup.name.clone()).filter(|n| !n.trim().is_empty()),
            };
            app.setup.error = None;
            app.model.add_device(request);
            app.page = Page::Setup(Step::Creating);
        }
    });
}

fn file_name(path: &std::path::Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.display().to_string())
}

/// The board an image is for, when it says (prepared folders and archive names like n72ap-7E18.itbase).
fn board_of(path: &std::path::Path) -> Option<String> {
    let lock = if path.is_dir() {
        [path.join("device.lock.json"), path.join("files/device.lock.json")].into_iter().find(|p| p.is_file())
    } else {
        None
    };
    if let Some(lock) = lock {
        let text = std::fs::read_to_string(lock).ok()?;
        let value: serde_json::Value = serde_json::from_str(&text).ok()?;
        return value["board"].as_str().map(str::to_string);
    }
    let name = path.file_name()?.to_string_lossy().to_string();
    name.split('-').next().filter(|b| b.ends_with("ap")).map(str::to_string)
}
