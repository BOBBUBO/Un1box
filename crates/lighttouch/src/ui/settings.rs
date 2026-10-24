//! Settings: grouped cards like the Android app's settings screen.
use super::{App, Page, icons, theme, widgets};
use eframe::egui::{self, RichText, Ui};
use lighttouch_model::settings::{Corner, OverlayGraph, OverlayLevel, Scaling, Theme};

pub fn show(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        ui.add_space(18.0);
        let width = ui.available_width().min(640.0);
        let margin = ((ui.available_width() - width) / 2.0).max(24.0);
        egui::Frame::new().inner_margin(egui::Margin::symmetric(margin as i8, 0)).show(ui, |ui| {
            ui.set_max_width(width);
            ui.horizontal(|ui| {
                if widgets::icon_button(ui, &p, icons::BACK, "Back").clicked() {
                    app.page = Page::Device;
                }
                ui.label(RichText::new("Settings").font(theme::headline()));
            });
            let before = app.model.settings.clone();
            let settings = &mut app.model.settings;

            widgets::section(ui, &p, "Display");
            widgets::card(ui, &p, |ui| {
                widgets::setting_row(ui, &p, icons::SUN, "Theme", "", |ui| {
                    widgets::segmented(
                        ui,
                        &p,
                        &mut settings.theme,
                        &[(Theme::System, "System"), (Theme::Dark, "Dark"), (Theme::Light, "Light")],
                    );
                });
                widgets::setting_row(
                    ui,
                    &p,
                    icons::MONITOR,
                    "Screen scaling",
                    "Smooth blends pixels; Sharp keeps them crisp",
                    |ui| {
                        widgets::segmented(
                            ui,
                            &p,
                            &mut settings.scaling,
                            &[(Scaling::Smooth, "Smooth"), (Scaling::Sharp, "Sharp")],
                        );
                    },
                );
                widgets::setting_row(
                    ui,
                    &p,
                    icons::PHONE,
                    "Show the device",
                    "Draw the iPod or iPhone around its screen",
                    |ui| {
                        widgets::switch(ui, &p, &mut settings.device_frame);
                    },
                );
                widgets::setting_row(
                    ui,
                    &p,
                    icons::PAUSE,
                    "Pause when minimized",
                    "Resumes when the window comes back",
                    |ui| {
                        widgets::switch(ui, &p, &mut settings.pause_when_hidden);
                    },
                );
            });

            widgets::section(ui, &p, "Audio");
            widgets::card(ui, &p, |ui| {
                widgets::setting_row(
                    ui,
                    &p,
                    icons::WAVEFORM,
                    "Headphones plugged in",
                    "iOS skips speaker protection, which saves CPU in games. Applies at the next start.",
                    |ui| {
                        widgets::switch(ui, &p, &mut settings.headphones);
                    },
                );
                widgets::setting_row(ui, &p, icons::VOLUME_UP, "Mute", "Silence the device without pausing it", |ui| {
                    widgets::switch(ui, &p, &mut settings.muted);
                });
            });

            widgets::section(ui, &p, "Performance overlay");
            widgets::card(ui, &p, |ui| {
                widgets::setting_row(ui, &p, icons::CHART, "Detail", "", |ui| {
                    widgets::segmented(
                        ui,
                        &p,
                        &mut settings.overlay,
                        &[
                            (OverlayLevel::Off, "Off"),
                            (OverlayLevel::Fps, "FPS"),
                            (OverlayLevel::Basic, "Basic"),
                            (OverlayLevel::Full, "Full"),
                        ],
                    );
                });
                if settings.overlay != OverlayLevel::Off {
                    widgets::setting_row(ui, &p, icons::CHART, "Graph", "", |ui| {
                        widgets::segmented(
                            ui,
                            &p,
                            &mut settings.overlay_graph,
                            &[(OverlayGraph::FrameTime, "Frame time"), (OverlayGraph::FrameRate, "Frame rate")],
                        );
                    });
                    widgets::setting_row(ui, &p, icons::MONITOR, "Position", "", |ui| {
                        widgets::segmented(
                            ui,
                            &p,
                            &mut settings.overlay_corner,
                            &[
                                (Corner::TopLeft, "↖"),
                                (Corner::TopRight, "↗"),
                                (Corner::BottomLeft, "↙"),
                                (Corner::BottomRight, "↘"),
                            ],
                        );
                    });
                    widgets::setting_row(ui, &p, icons::SUN, "Background", "", |ui| {
                        ui.add(egui::Slider::new(&mut settings.overlay_opacity, 0.0..=0.9).show_value(false));
                    });
                    widgets::setting_row(
                        ui,
                        &p,
                        icons::PHONE,
                        "Show in device view",
                        "Otherwise only in full screen",
                        |ui| {
                            widgets::switch(ui, &p, &mut settings.overlay_in_device_view);
                        },
                    );
                }
            });

            widgets::section(ui, &p, "Diagnostics");
            widgets::card(ui, &p, |ui| {
                widgets::setting_row(
                    ui,
                    &p,
                    icons::CHART,
                    "Inspect button",
                    "Adds the frame inspector to the controls",
                    |ui| {
                        widgets::switch(ui, &p, &mut settings.inspect_button);
                    },
                );
                widgets::setting_row(ui, &p, icons::FILE, "Session logs", "What each device session printed", |ui| {
                    if widgets::tonal(ui, &p, "Open folder").clicked() {
                        let dir = app.model.dirs.logs();
                        let _ = std::fs::create_dir_all(&dir);
                        open_path(&dir);
                    }
                });
            });

            let session_active = app.model.session_active();
            let selected = app.model.selected().cloned();
            widgets::section(ui, &p, "Device");
            widgets::card(ui, &p, |ui| {
                if let Some(device) = &selected {
                    widgets::setting_row(
                        ui,
                        &p,
                        icons::RECOVER,
                        "Recover retained state",
                        "Cold boot the selected device with its data as it was left",
                        |ui| {
                            if widgets::tonal_enabled(ui, &p, "Recover", !session_active).clicked() {
                                app.model.start(true);
                                app.page = Page::Device;
                            }
                        },
                    );
                    let _ = device;
                }
            });

            widgets::section(ui, &p, "About");
            widgets::card(ui, &p, |ui| {
                widgets::setting_row(
                    ui,
                    &p,
                    icons::INFO,
                    &format!("LightTouch {}", env!("CARGO_PKG_VERSION")),
                    "Free software under the GNU GPL, version 2 or later",
                    |ui| {
                        if widgets::tonal(ui, &p, "Source code").clicked() {
                            open_path(std::path::Path::new("https://github.com/blake-lucas/LightTouch"));
                        }
                    },
                );
            });
            ui.add_space(28.0);

            if app.model.settings != before {
                let muted_changed = app.model.settings.muted != before.muted;
                app.model.save_settings();
                if muted_changed {
                    let muted = app.model.settings.muted;
                    app.model.set_muted(muted);
                }
            }
        });
    });
}

/// Open a folder or link with the desktop's default handler.
pub fn open_path(path: &std::path::Path) {
    #[cfg(target_os = "linux")]
    let program = "xdg-open";
    #[cfg(target_os = "macos")]
    let program = "open";
    #[cfg(windows)]
    let program = "explorer";
    let _ = std::process::Command::new(program).arg(path).spawn();
}
