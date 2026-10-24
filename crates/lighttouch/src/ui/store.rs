//! The Legacy Store page: search the archive for the current device, pick a version that runs on it, install it,
//! and follow the install. Also installs a local IPA and shows the newest crash report. Mirrors the Android store.
use super::{App, Page, icons, theme, widgets};
use eframe::egui::{self, Align2, CornerRadius, FontId, Margin, RichText, Sense, Ui, vec2};
use lighttouch_catalog::catalog::short_product_name;
use lighttouch_catalog::store::StoreApp;
use lighttouch_model::store::{Icon, byte_size};

fn page(ui: &mut Ui, add: impl FnOnce(&mut Ui)) {
    egui::ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
        let w = ui.available_width().min(720.0);
        let margin = ((ui.available_width() - w) / 2.0).max(24.0);
        egui::Frame::new().inner_margin(Margin { left: margin as i8, right: margin as i8, top: 24, bottom: 24 }).show(
            ui,
            |ui| {
                ui.set_max_width(w);
                add(ui);
            },
        );
    });
}

pub fn show(app: &mut App, ui: &mut Ui) {
    page(ui, |ui| {
        header(app, ui);
        status_cards(app, ui);
        if app.model.store.chosen.is_some() {
            details(app, ui);
        } else {
            search(app, ui);
        }
    });
}

fn header(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    let subtitle = match &app.model.store.target {
        Some(t) => format!("{} · iOS {}", short_product_name(&t.product_type), t.ios),
        None => "No device selected".into(),
    };
    ui.horizontal(|ui| {
        if widgets::icon_button(ui, &p, icons::BACK, "Back").clicked() {
            if app.model.store.chosen.is_some() {
                app.model.store.close();
            } else {
                app.page = Page::Device;
            }
        }
        ui.vertical(|ui| {
            ui.label(RichText::new("Legacy Store").font(theme::headline()));
            ui.label(widgets::muted(&p, subtitle));
        });
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            let connected = app.model.store.guest_connected();
            let more = widgets::icon_button(ui, &p, icons::MORE, "More");
            egui::Popup::menu(&more).show(|ui| {
                ui.set_min_width(200.0);
                let installing = app.model.store.installing();
                if ui
                    .add_enabled(
                        connected && !installing,
                        egui::Button::new(widgets::icon_label(icons::FILE, "Install an IPA file…", None)),
                    )
                    .clicked()
                {
                    if let Some(file) =
                        rfd::FileDialog::new().set_title("Choose an IPA").add_filter("iOS app", &["ipa"]).pick_file()
                    {
                        app.model.store.install_file(file);
                    }
                }
                if ui
                    .add_enabled(
                        connected,
                        egui::Button::new(widgets::icon_label(icons::INFO, "Show last crash", None)),
                    )
                    .clicked()
                {
                    app.model.store.show_last_crash();
                }
            });
        });
    });
    ui.add_space(14.0);
}

/// The install in progress (or its result), the crash report, and whether installs can reach the device.
fn status_cards(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    if let Some(job) = app.model.store.job.clone() {
        widgets::card(ui, &p, |ui| {
            ui.horizontal(|ui| {
                let (icon, color) = if job.error.is_some() {
                    (icons::WARNING, theme::WARNING)
                } else if job.done {
                    (icons::CHECK_CIRCLE, p.primary)
                } else {
                    (icons::DOWNLOAD, p.on_surface_variant)
                };
                ui.label(widgets::icon_text(icon, 22.0).color(color));
                ui.add_space(4.0);
                ui.vertical(|ui| {
                    ui.label(RichText::new(&job.name).font(theme::title()));
                    match &job.error {
                        Some(error) => ui.add(egui::Label::new(RichText::new(error).color(p.error)).wrap()),
                        None => ui.label(widgets::muted(&p, &job.phase)),
                    };
                });
            });
            if job.active() {
                ui.add_space(8.0);
                widgets::progress(ui, &p, job.progress, ui.available_width());
            }
            if job.mutating && job.active() {
                ui.add_space(6.0);
                ui.label(widgets::muted(&p, "Keep the device running until this finishes.").size(12.5));
            }
            if job.cancellable || !job.active() {
                ui.add_space(4.0);
                ui.horizontal(|ui| {
                    if job.cancellable && widgets::text_button(ui, &p, "Cancel").clicked() {
                        app.model.store.cancel();
                    }
                    if !job.active() && widgets::text_button(ui, &p, "OK").clicked() {
                        app.model.store.dismiss_job();
                    }
                });
            }
        });
        ui.add_space(12.0);
    }
    if let Some(crash) = app.model.store.crash.clone() {
        widgets::card(ui, &p, |ui| {
            ui.label(RichText::new("Last crash").font(theme::title()));
            ui.add_space(6.0);
            egui::ScrollArea::vertical().max_height(260.0).id_salt("crash").show(ui, |ui| {
                ui.add(egui::Label::new(RichText::new(&crash).monospace().size(12.0)).wrap());
            });
            ui.add_space(4.0);
            if widgets::text_button(ui, &p, "Close").clicked() {
                app.model.store.dismiss_crash();
            }
        });
        ui.add_space(12.0);
    }
    if !app.model.store.guest_connected() {
        let text = if app.model.session_active() {
            "Waiting for the device to finish starting before apps can be installed."
        } else {
            "You can browse now. Start the device to install apps."
        };
        ui.horizontal(|ui| {
            ui.label(widgets::icon_text(icons::INFO, 16.0).color(p.on_surface_variant));
            ui.label(widgets::muted(&p, text));
        });
        ui.add_space(10.0);
    }
}

fn search(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    let mut submit = false;
    ui.horizontal(|ui| {
        let pill = egui::Frame::new()
            .fill(p.container_high)
            .corner_radius(CornerRadius::same(theme::radius::PILL))
            .inner_margin(Margin::symmetric(14, 8))
            .show(ui, |ui| {
                ui.horizontal_centered(|ui| {
                    // Phosphor's glyphs sit high in their line box.
                    ui.vertical(|ui| {
                        ui.add_space(5.0);
                        ui.label(widgets::icon_text(icons::SEARCH, 18.0).color(p.on_surface_variant));
                    });
                    let edit = ui.add(
                        egui::TextEdit::singleline(&mut app.model.store.query)
                            .hint_text("Search apps and games")
                            .frame(egui::Frame::NONE)
                            .vertical_align(egui::Align::Center)
                            .desired_width(ui.available_width().min(380.0))
                            .margin(vec2(4.0, 2.0)),
                    );
                    submit = edit.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
                    if app.model.store.searching {
                        widgets::spinner(ui, &p, 16.0);
                    }
                    edit
                })
                .inner
            });
        // The whole pill focuses the field, not just its text.
        let edit = pill.inner;
        if pill.response.interact(Sense::click()).clicked() {
            edit.request_focus();
        }
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            widgets::switch(ui, &p, &mut app.model.store.show_incompatible);
            ui.label(widgets::muted(&p, "Include apps that won't run"));
        });
    });
    if submit {
        app.model.store.search();
    }
    ui.add_space(12.0);
    if !app.model.store.message.is_empty() {
        ui.label(widgets::muted(&p, &app.model.store.message));
        ui.add_space(8.0);
    }
    let rows: Vec<StoreApp> = app.model.store.visible_results().into_iter().cloned().collect();
    if rows.is_empty() {
        if !app.model.store.searching && app.model.store.message.is_empty() {
            let filtered = !app.model.store.results.is_empty();
            ui.add_space(30.0);
            ui.vertical_centered(|ui| {
                ui.label(widgets::icon_text(icons::BAG, 40.0).color(p.on_surface_variant));
                ui.add_space(6.0);
                ui.label(
                    RichText::new(if filtered { "Nothing here runs on this device" } else { "Find apps and games" })
                        .font(theme::title()),
                );
                ui.label(widgets::muted(
                    &p,
                    if filtered {
                        "Include apps that won't run to see every result."
                    } else {
                        "Search the archive of classic iOS apps. Versions that run on this device are picked for you."
                    },
                ));
            });
        }
        return;
    }
    widgets::card(ui, &p, |ui| {
        for row in rows {
            if result_row(app, ui, &row).clicked() {
                app.model.store.open(&row);
            }
        }
    });
}

fn app_icon(app: &mut App, ui: &mut Ui, url: Option<&str>, size: f32) {
    let p = app.palette;
    let (rect, _) = ui.allocate_exact_size(vec2(size, size), Sense::hover());
    let radius = CornerRadius::same((size / 4.5) as u8);
    ui.painter().rect_filled(rect, radius, p.container_highest);
    if let Some(url) = url {
        if let Icon::Ready(bytes) = app.model.store.icon(url) {
            egui::Image::from_bytes(format!("bytes://store-icon/{url}"), egui::load::Bytes::Shared(bytes))
                .corner_radius(radius)
                .paint_at(ui, rect);
        }
    }
}

fn result_row(app: &mut App, ui: &mut Ui, row: &StoreApp) -> egui::Response {
    let p = app.palette;
    let problem = row.incompatibility();
    let installed = app.model.store.installed.get(&row.bundle_id).cloned();
    let (rect, response) = ui.allocate_exact_size(vec2(ui.available_width(), 64.0), Sense::click());
    if response.hovered() {
        ui.painter().rect_filled(
            rect.expand2(vec2(8.0, 0.0)),
            CornerRadius::same(theme::radius::ROW),
            p.container_high,
        );
    }
    let tag = match (&problem, &installed) {
        (Some(problem), _) => Some((problem.clone(), p.error)),
        (None, Some(version)) => Some((format!("Installed {version}"), p.primary)),
        _ => None,
    };
    let tag_galley =
        tag.map(|(text, color)| ui.painter().layout_no_wrap(text, FontId::new(12.5, theme::medium()), color));
    let reserved = 34.0 + tag_galley.as_ref().map(|g| g.size().x + 12.0).unwrap_or(0.0);
    let text_rect = egui::Rect::from_min_max(rect.min, egui::pos2(rect.max.x - reserved, rect.max.y));
    let mut child = ui
        .new_child(egui::UiBuilder::new().max_rect(text_rect).layout(egui::Layout::left_to_right(egui::Align::Center)));
    child.multiply_opacity(if problem.is_some() { 0.55 } else { 1.0 });
    app_icon(app, &mut child, row.icon_url.as_deref(), 46.0);
    child.add_space(12.0);
    child.vertical(|ui| {
        ui.add_space(9.0);
        ui.add(egui::Label::new(RichText::new(&row.name).family(theme::medium()).size(14.5)).truncate());
        let details: Vec<String> =
            [row.developer.clone(), row.version.clone(), row.size.filter(|s| *s > 0).map(byte_size)]
                .into_iter()
                .flatten()
                .collect();
        ui.add(egui::Label::new(widgets::muted(&p, details.join(" · ")).size(12.5)).truncate());
    });
    let painter = ui.painter();
    let right = rect.right_center() - vec2(8.0, 0.0);
    painter.text(
        right,
        Align2::RIGHT_CENTER,
        icons::CARET_RIGHT,
        FontId::new(16.0, theme::icons()),
        p.on_surface_variant,
    );
    if let Some(galley) = tag_galley {
        let pos = right - vec2(26.0 + galley.size().x, galley.size().y / 2.0);
        painter.galley(pos, galley, p.on_surface);
    }
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

fn details(app: &mut App, ui: &mut Ui) {
    let p = app.palette;
    let Some(chosen) = app.model.store.chosen.clone() else { return };
    let installed = app.model.store.installed.get(&chosen.bundle_id).cloned();
    ui.horizontal(|ui| {
        app_icon(app, ui, chosen.icon_url.as_deref(), 72.0);
        ui.add_space(12.0);
        ui.vertical(|ui| {
            ui.add_space(6.0);
            ui.add(egui::Label::new(RichText::new(&chosen.name).font(theme::title()).size(19.0)).wrap());
            if let Some(developer) = &chosen.developer {
                ui.label(widgets::muted(&p, developer));
            }
            if let Some(version) = &installed {
                ui.label(RichText::new(format!("Installed {version}")).color(p.primary).size(12.5));
            }
        });
    });
    ui.add_space(14.0);
    let store = &app.model.store;
    let ready = store.candidate.is_some() && store.guest_connected() && !store.installing();
    ui.horizontal(|ui| {
        if widgets::primary_enabled(ui, &p, if installed.is_some() { "Reinstall" } else { "Install" }, ready).clicked()
        {
            app.model.store.install_candidate();
        }
        let store = &app.model.store;
        let selected = store.copies.iter().flatten().find(|c| Some(c.ipa_id) == store.selected);
        if let Some(problem) = store.problem.clone() {
            ui.add(egui::Label::new(RichText::new(problem).color(p.error)).wrap());
        } else if store.selected.is_some() && store.candidate.is_none() {
            widgets::spinner(ui, &p, 18.0);
            ui.label(widgets::muted(&p, "Checking this version…"));
        } else if let Some(copy) = selected {
            let size = copy.size.map(|s| format!(" · {}", byte_size(s))).unwrap_or_default();
            ui.label(widgets::muted(&p, format!("Version {}{size}", copy.version)));
        }
    });
    widgets::section(ui, &p, "Versions that run on this device");
    match app.model.store.copies.clone() {
        None => {
            ui.horizontal(|ui| {
                widgets::spinner(ui, &p, 18.0);
                ui.label(widgets::muted(&p, "Loading versions…"));
            });
        }
        Some(copies) if copies.is_empty() => {
            ui.label(widgets::muted(&p, "None found."));
        }
        Some(copies) => {
            widgets::card(ui, &p, |ui| {
                for (i, copy) in copies.iter().enumerate() {
                    let twin = copies.iter().enumerate().any(|(j, c)| j != i && c.version == copy.version);
                    let selected = app.model.store.selected == Some(copy.ipa_id);
                    let (rect, response) = ui.allocate_exact_size(vec2(ui.available_width(), 46.0), Sense::click());
                    if selected || response.hovered() {
                        let fill = if selected { p.primary_container } else { p.container_high };
                        ui.painter().rect_filled(
                            rect.expand2(vec2(8.0, 0.0)),
                            CornerRadius::same(theme::radius::ROW),
                            fill,
                        );
                    }
                    let painter = ui.painter();
                    let title =
                        if twin { format!("{} · copy {}", copy.version, copy.ipa_id) } else { copy.version.clone() };
                    let subtitle: Vec<String> =
                        [copy.size.map(byte_size), copy.minimum_os.as_ref().map(|m| format!("iOS {m} or later"))]
                            .into_iter()
                            .flatten()
                            .collect();
                    let text_color = if selected { p.on_primary_container } else { p.on_surface };
                    painter.text(
                        rect.left_center() + vec2(36.0, -8.0),
                        Align2::LEFT_CENTER,
                        title,
                        FontId::new(14.5, theme::medium()),
                        text_color,
                    );
                    painter.text(
                        rect.left_center() + vec2(36.0, 10.0),
                        Align2::LEFT_CENTER,
                        subtitle.join(" · "),
                        FontId::new(12.0, egui::FontFamily::Proportional),
                        if selected { p.on_primary_container } else { p.on_surface_variant },
                    );
                    let mark = if selected { icons::CHECK_CIRCLE } else { icons::CIRCLE };
                    painter.text(
                        rect.left_center() + vec2(12.0, 0.0),
                        Align2::CENTER_CENTER,
                        mark,
                        FontId::new(18.0, theme::icons()),
                        if selected { p.primary } else { p.outline },
                    );
                    if response.on_hover_cursor(egui::CursorIcon::PointingHand).clicked() && !selected {
                        app.model.store.choose(copy.ipa_id);
                    }
                }
            });
        }
    }
}
