//! The egui frontend. It only draws `lighttouch_model::Model` and calls its methods; behaviour lives in the model so
//! another toolkit could replace this module.
mod device_frame;
mod fullscreen;
mod guest;
mod home;
pub mod icons;
mod overlay;
mod prepare;
mod settings;
mod setup;
mod sidebar;
pub mod theme;
mod widgets;

use eframe::egui::{self, Align2, Color32, CornerRadius, Frame, Margin, RichText, vec2};
use lighttouch_core::paths::{DataDirs, Resources, data_root};
use lighttouch_model::settings::Theme;
use lighttouch_model::{Finished, Model};
use std::sync::Arc;
use std::time::{Duration, Instant};
use theme::Palette;

#[derive(Debug, Clone, PartialEq)]
pub enum Page {
    Device,
    Settings,
    Setup(setup::Step),
    /// Prepare from an IPSW (its step lives in `App::prepare`).
    Prepare,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Dialog {
    Stop,
    Rename { id: String, name: String },
    Delete { id: String, name: String },
}

pub struct App {
    pub model: Model,
    pub guest: guest::GuestView,
    pub page: Page,
    pub dialog: Option<Dialog>,
    pub fullscreen: bool,
    pub palette: Palette,
    applied: Option<(Theme, bool)>,
    pub setup: setup::SetupState,
    pub prepare: prepare::PrepareState,
    /// When the window was minimized with the guest running (to resume on return).
    paused_for_hidden: bool,
    /// A Home press that releases itself (waking the screen).
    pulse_release: Option<Instant>,
}

pub fn run() -> eframe::Result {
    let viewport = egui::ViewportBuilder::default()
        .with_title("LightTouch")
        .with_app_id("org.lighttouch.LightTouch")
        .with_inner_size([1120.0, 780.0])
        .with_min_inner_size([760.0, 560.0]);
    let options = eframe::NativeOptions { viewport, centered: true, ..Default::default() };
    eframe::run_native(
        "LightTouch",
        options,
        Box::new(|cc| {
            theme::install_fonts(&cc.egui_ctx);
            let ctx = cc.egui_ctx.clone();
            let waker: lighttouch_model::Waker = Arc::new(move || ctx.request_repaint());
            let model = Model::new(DataDirs::new(data_root()), Resources::locate(), waker);
            Ok(Box::new(App::new(model)))
        }),
    )
}

impl App {
    pub fn new(model: Model) -> Self {
        let page = if model.devices.is_empty() { Page::Setup(setup::Step::Welcome) } else { Page::Device };
        Self {
            model,
            guest: guest::GuestView::default(),
            page,
            dialog: None,
            fullscreen: false,
            palette: theme::DARK,
            applied: None,
            setup: setup::SetupState::default(),
            prepare: prepare::PrepareState::default(),
            paused_for_hidden: false,
            pulse_release: None,
        }
    }

    fn apply_theme(&mut self, ctx: &egui::Context) {
        let dark = match self.model.settings.theme {
            Theme::Dark => true,
            Theme::Light => false,
            Theme::System => ctx.system_theme().is_none_or(|t| t == egui::Theme::Dark),
        };
        let key = (self.model.settings.theme, dark);
        if self.applied != Some(key) {
            self.palette = if dark { theme::DARK } else { theme::LIGHT };
            theme::apply(ctx, &self.palette);
            self.applied = Some(key);
        }
    }

    /// Press Home briefly (wakes the screen like a tap on the Home button).
    pub fn pulse_home(&mut self) {
        self.guest.set_button(&mut self.model, device_frame::HOME, true);
        self.pulse_release = Some(Instant::now() + Duration::from_millis(100));
    }

    fn background_work(&mut self, ctx: &egui::Context) {
        let was_active = self.model.session_active();
        self.model.tick();
        if was_active && !self.model.session_active() {
            self.guest.clear();
            if self.fullscreen {
                self.set_fullscreen(ctx, false);
            }
        }
        self.guest.update(ctx, &mut self.model);
        if let Some(at) = self.pulse_release {
            if Instant::now() >= at {
                self.pulse_release = None;
                self.guest.set_button(&mut self.model, device_frame::HOME, false);
            } else {
                ctx.request_repaint_after(Duration::from_millis(20));
            }
        }
        if let Some(finished) = self.model.take_finished() {
            match finished {
                Finished::DeviceAdded { name, timings, .. } => {
                    if matches!(self.page, Page::Setup(_) | Page::Prepare) {
                        self.setup.added = Some(name);
                        self.setup.timings = timings;
                        self.page = Page::Setup(setup::Step::Done);
                        self.prepare = prepare::PrepareState::default();
                    } else {
                        self.model.notice = Some((format!("{name} is ready"), Instant::now()));
                    }
                }
                Finished::Failed { activity: lighttouch_model::Activity::Preparing(_), message } => {
                    self.prepare.error = Some(message);
                    self.prepare.step = prepare::Step::Progress;
                    self.page = Page::Prepare;
                }
                Finished::Failed { message, .. } => {
                    if matches!(self.page, Page::Setup(setup::Step::Creating)) {
                        self.setup.error = Some(message);
                        self.page = Page::Setup(setup::Step::Files);
                    } else {
                        self.model.error = Some(message);
                    }
                }
            }
        }
        // Pause while minimized, resume on return.
        let minimized = ctx.input(|i| i.viewport().minimized.unwrap_or(false));
        if self.model.settings.pause_when_hidden {
            if minimized && !self.paused_for_hidden {
                if let Some(s) = &self.model.session {
                    if matches!(s.phase, lighttouch_model::session::Phase::Running) {
                        self.model.set_paused(true);
                        self.paused_for_hidden = true;
                    }
                }
            } else if !minimized && self.paused_for_hidden {
                self.paused_for_hidden = false;
                self.model.set_paused(false);
            }
        }
        // Keep progress and timers moving without a busy loop.
        if self.model.busy().is_some() || self.model.session.is_some() || self.model.notice.is_some() {
            ctx.request_repaint_after(Duration::from_millis(250));
        }
    }

    pub fn set_fullscreen(&mut self, ctx: &egui::Context, on: bool) {
        self.fullscreen = on;
        ctx.send_viewport_cmd(egui::ViewportCommand::Fullscreen(on));
    }

    fn dialogs(&mut self, ctx: &egui::Context) {
        let Some(dialog) = self.dialog.clone() else { return };
        let p = self.palette;
        let mut close = false;
        let modal = egui::Modal::new(egui::Id::unique("dialog")).backdrop_color(Color32::from_black_alpha(140)).frame(
            Frame::new()
                .fill(p.container_high)
                .corner_radius(CornerRadius::same(theme::radius::DOCK))
                .inner_margin(Margin::same(24)),
        );
        let response = modal.show(ctx, |ui| {
            ui.set_width(380.0);
            match dialog {
                Dialog::Stop => {
                    let name = self.model.session.as_ref().map(|s| s.name.clone()).unwrap_or_default();
                    ui.label(widgets::icon_text(icons::POWER, 26.0).color(p.primary));
                    ui.add_space(6.0);
                    ui.label(RichText::new(format!("Stop {name}?")).font(theme::title()));
                    ui.add_space(4.0);
                    ui.label(widgets::muted(
                        &p,
                        "iOS shuts down like holding the power button: apps close and the device turns off. Your apps and data are kept.",
                    ));
                    ui.add_space(16.0);
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if widgets::danger(ui, "Shut down").clicked() {
                            self.model.stop();
                            close = true;
                        }
                        if widgets::text_button(ui, &p, "Cancel").clicked() {
                            close = true;
                        }
                    });
                }
                Dialog::Rename { id, mut name } => {
                    ui.label(RichText::new("Rename device").font(theme::title()));
                    ui.add_space(10.0);
                    let edit = ui.add(egui::TextEdit::singleline(&mut name).desired_width(f32::INFINITY).margin(vec2(10.0, 8.0)));
                    if !edit.has_focus() && !edit.lost_focus() {
                        edit.request_focus();
                    }
                    let fits = lighttouch_catalog::catalog::fits_device_name(&name) && !name.trim().is_empty();
                    let submit = edit.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
                    ui.add_space(16.0);
                    let mut save = false;
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        save = widgets::primary_enabled(ui, &p, "Save", fits).clicked();
                        if widgets::text_button(ui, &p, "Cancel").clicked() {
                            close = true;
                        }
                    });
                    if (save || submit) && fits {
                        self.model.rename(&id, &name);
                        close = true;
                    } else if !close {
                        self.dialog = Some(Dialog::Rename { id, name });
                    }
                }
                Dialog::Delete { id, name } => {
                    ui.label(widgets::icon_text(icons::TRASH, 26.0).color(theme::DANGER));
                    ui.add_space(6.0);
                    ui.label(RichText::new(format!("Delete {name}?")).font(theme::title()));
                    ui.add_space(4.0);
                    ui.label(widgets::muted(&p, "Its apps, settings and data are removed from this computer. This can't be undone."));
                    ui.add_space(16.0);
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if widgets::danger(ui, "Delete").clicked() {
                            self.model.remove(&id);
                            close = true;
                            if self.model.devices.is_empty() {
                                self.page = Page::Setup(setup::Step::Welcome);
                            }
                        }
                        if widgets::text_button(ui, &p, "Cancel").clicked() {
                            close = true;
                        }
                    });
                }
            }
        });
        if close || response.should_close() {
            self.dialog = None;
        }
    }

    fn notices(&mut self, ctx: &egui::Context) {
        let p = self.palette;
        if let Some((text, _)) = &self.model.notice {
            egui::Area::new(egui::Id::unique("notice"))
                .anchor(Align2::CENTER_BOTTOM, vec2(0.0, -28.0))
                .order(egui::Order::Tooltip)
                .show(ctx, |ui| {
                    Frame::new()
                        .fill(p.container_highest)
                        .corner_radius(CornerRadius::same(theme::radius::ROW))
                        .inner_margin(Margin::symmetric(16, 10))
                        .shadow(ctx.global_style().visuals.popup_shadow)
                        .show(ui, |ui| ui.label(RichText::new(text.as_str()).color(p.on_surface)));
                });
        }
        if let Some(error) = self.model.error.clone() {
            let mut dismiss = false;
            egui::Area::new(egui::Id::unique("error"))
                .anchor(Align2::CENTER_TOP, vec2(if self.fullscreen { 0.0 } else { 140.0 }, 72.0))
                .order(egui::Order::Foreground)
                .show(ctx, |ui| {
                    Frame::new()
                        .fill(p.error_container)
                        .corner_radius(CornerRadius::same(theme::radius::ROW))
                        .inner_margin(Margin::symmetric(14, 10))
                        .show(ui, |ui| {
                            ui.set_max_width(560.0);
                            ui.horizontal(|ui| {
                                ui.label(widgets::icon_text(icons::WARNING, 18.0).color(p.on_error_container));
                                ui.add(egui::Label::new(RichText::new(&error).color(p.on_error_container)).wrap());
                                if widgets::icon_button_sized(ui, &p, icons::CLOSE, "Dismiss", 28.0, true).clicked() {
                                    dismiss = true;
                                }
                            });
                        });
                });
            if dismiss {
                self.model.dismiss_error();
            }
        }
    }
}

impl eframe::App for App {
    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // Before the frame's root Ui exists, so it picks up a changed theme.
        self.apply_theme(ctx);
        self.background_work(ctx);
    }

    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if self.fullscreen {
            fullscreen::show(self, ui);
        } else {
            sidebar::show(self, ui);
            let p = self.palette;
            egui::CentralPanel::default().frame(Frame::new().fill(p.background)).show(ui, |ui| {
                match self.page.clone() {
                    Page::Device => home::show(self, ui),
                    Page::Settings => settings::show(self, ui),
                    Page::Setup(step) => setup::show(self, ui, step),
                    Page::Prepare => prepare::show(self, ui),
                }
            });
        }
        self.dialogs(&ctx);
        self.notices(&ctx);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        // Ask a running iOS to shut down cleanly; its worker finishes on its own.
        self.model.stop();
    }
}
