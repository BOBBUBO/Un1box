//! The app's reusable controls, styled from the palette. Buttons size to their label (never full width).
use super::theme::{self, Palette, radius};
use eframe::egui::{
    self, Align2, Color32, CornerRadius, FontId, Frame, Margin, Response, RichText, Sense, Stroke, Ui, Vec2, vec2,
};

pub fn icon_text(icon: &str, size: f32) -> RichText {
    RichText::new(icon).family(theme::icons()).size(size)
}

fn button(ui: &mut Ui, text: RichText, fill: Color32, stroke: Stroke, height: f32, enabled: bool) -> Response {
    let button = egui::Button::new(text)
        .fill(fill)
        .stroke(stroke)
        .corner_radius(CornerRadius::same((height / 2.0) as u8))
        .min_size(vec2(0.0, height));
    ui.scope(|ui| {
        ui.spacing_mut().button_padding = vec2(height * 0.5, 0.0);
        ui.add_enabled(enabled, button)
    })
    .inner
}

/// The main action on a screen.
pub fn primary(ui: &mut Ui, p: &Palette, text: &str) -> Response {
    primary_enabled(ui, p, text, true)
}

pub fn primary_enabled(ui: &mut Ui, p: &Palette, text: &str, enabled: bool) -> Response {
    let color = if enabled { p.on_primary } else { p.on_surface_variant };
    let fill = if enabled { p.primary } else { p.container_highest };
    button(ui, RichText::new(text).color(color).size(15.0).family(theme::semibold()), fill, Stroke::NONE, 44.0, enabled)
}

/// A secondary action: tinted container.
pub fn tonal(ui: &mut Ui, p: &Palette, text: &str) -> Response {
    tonal_enabled(ui, p, text, true)
}

pub fn tonal_enabled(ui: &mut Ui, p: &Palette, text: &str, enabled: bool) -> Response {
    button(ui, RichText::new(text).color(p.on_surface), p.container_highest, Stroke::NONE, 40.0, enabled)
}

/// A destructive confirmation ("Shut down", "Delete").
pub fn danger(ui: &mut Ui, text: &str) -> Response {
    button(
        ui,
        RichText::new(text).color(Color32::WHITE).family(theme::semibold()),
        theme::DANGER.gamma_multiply(0.92),
        Stroke::NONE,
        40.0,
        true,
    )
}

/// A low-emphasis action: coloured text only.
pub fn text_button(ui: &mut Ui, p: &Palette, text: &str) -> Response {
    let r = ui.add(
        egui::Button::new(RichText::new(text).color(p.primary).family(theme::medium()))
            .frame(false)
            .min_size(vec2(0.0, 32.0)),
    );
    r.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// A round icon-only button.
pub fn icon_button(ui: &mut Ui, p: &Palette, icon: &str, tooltip: &str) -> Response {
    icon_button_sized(ui, p, icon, tooltip, 38.0, true)
}

pub fn icon_button_sized(ui: &mut Ui, p: &Palette, icon: &str, tooltip: &str, size: f32, enabled: bool) -> Response {
    let (rect, response) =
        ui.allocate_exact_size(Vec2::splat(size), if enabled { Sense::click() } else { Sense::hover() });
    if ui.is_rect_visible(rect) {
        let fill = if response.is_pointer_button_down_on() {
            p.outline
        } else if response.hovered() && enabled {
            p.container_highest
        } else {
            Color32::TRANSPARENT
        };
        ui.painter().circle_filled(rect.center(), size / 2.0, fill);
        let color = if enabled { p.on_surface } else { p.on_surface_variant.gamma_multiply(0.5) };
        ui.painter().text(rect.center(), Align2::CENTER_CENTER, icon, FontId::new(size * 0.52, theme::icons()), color);
    }
    let response = if enabled { response.on_hover_cursor(egui::CursorIcon::PointingHand) } else { response };
    response.on_hover_text(tooltip)
}

/// A raised group of rows or content.
pub fn card<R>(ui: &mut Ui, p: &Palette, add: impl FnOnce(&mut Ui) -> R) -> R {
    Frame::new()
        .fill(p.container)
        .corner_radius(CornerRadius::same(radius::CARD))
        .inner_margin(Margin::same(18))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            add(ui)
        })
        .inner
}

/// A clickable card (e.g. "Prepare from an IPSW").
pub fn action_card(ui: &mut Ui, p: &Palette, icon: &str, title: &str, subtitle: &str, emphasized: bool) -> Response {
    let width = ui.available_width();
    let height = 76.0;
    let (rect, response) = ui.allocate_exact_size(vec2(width, height), Sense::click());
    let fill = match (emphasized, response.hovered()) {
        (true, false) => p.primary_container,
        (true, true) => p.primary_container.gamma_multiply(1.15),
        (false, false) => p.container,
        (false, true) => p.container_high,
    };
    let painter = ui.painter();
    painter.rect_filled(rect, CornerRadius::same(radius::CARD), fill);
    let icon_center = rect.left_center() + vec2(38.0, 0.0);
    painter.circle_filled(icon_center, 22.0, if emphasized { p.primary } else { p.container_highest });
    painter.text(
        icon_center,
        Align2::CENTER_CENTER,
        icon,
        FontId::new(22.0, theme::icons()),
        if emphasized { p.on_primary } else { p.on_surface },
    );
    let text_left = rect.left() + 76.0;
    let title_color = if emphasized { p.on_primary_container } else { p.on_surface };
    painter.text(
        egui::pos2(text_left, rect.center().y - 10.0),
        Align2::LEFT_CENTER,
        title,
        FontId::new(15.0, theme::semibold()),
        title_color,
    );
    painter.text(
        egui::pos2(text_left, rect.center().y + 11.0),
        Align2::LEFT_CENTER,
        subtitle,
        FontId::new(13.0, egui::FontFamily::Proportional),
        if emphasized { p.on_primary_container.gamma_multiply(0.8) } else { p.on_surface_variant },
    );
    painter.text(
        rect.right_center() - vec2(22.0, 0.0),
        Align2::CENTER_CENTER,
        super::icons::CARET_RIGHT,
        FontId::new(18.0, theme::icons()),
        title_color.gamma_multiply(0.7),
    );
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// A group header above a card ("Display", "Controls").
pub fn section(ui: &mut Ui, p: &Palette, text: &str) {
    ui.add_space(14.0);
    ui.label(RichText::new(text).font(theme::label()).color(p.primary));
    ui.add_space(2.0);
}

pub fn status_dot(ui: &mut Ui, color: Color32, size: f32) {
    let (rect, _) = ui.allocate_exact_size(Vec2::splat(size), Sense::hover());
    ui.painter().circle_filled(rect.center(), size / 2.0, color);
}

/// An on/off switch.
pub fn switch(ui: &mut Ui, p: &Palette, on: &mut bool) -> Response {
    let size = vec2(44.0, 24.0);
    let (rect, mut response) = ui.allocate_exact_size(size, Sense::click());
    if response.clicked() {
        *on = !*on;
        response.mark_changed();
    }
    let t = ui.ctx().animate_bool_responsive(response.id, *on);
    let track = if *on { p.primary } else { p.container_highest };
    let painter = ui.painter();
    painter.rect(
        rect,
        CornerRadius::same(12),
        track,
        Stroke::new(1.0, if *on { track } else { p.outline }),
        egui::StrokeKind::Inside,
    );
    let x = egui::lerp(rect.left() + 12.0..=rect.right() - 12.0, t);
    let knob = if *on { p.on_primary } else { p.on_surface_variant };
    painter.circle_filled(egui::pos2(x, rect.center().y), if *on { 8.0 } else { 6.0 }, knob);
    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// A row of mutually exclusive choices. Returns true when the choice changed.
pub fn segmented<T: PartialEq + Copy>(ui: &mut Ui, p: &Palette, value: &mut T, options: &[(T, &str)]) -> bool {
    let mut changed = false;
    Frame::new().fill(p.container_highest).corner_radius(CornerRadius::same(18)).inner_margin(Margin::same(3)).show(
        ui,
        |ui| {
            // Always left to right, even inside a right-aligned row.
            ui.with_layout(egui::Layout::left_to_right(egui::Align::Center), |ui| {
                ui.spacing_mut().item_spacing.x = 2.0;
                for (option, label) in options {
                    let selected = *value == *option;
                    let text =
                        RichText::new(*label).color(if selected { p.on_primary } else { p.on_surface }).size(13.0);
                    let r = ui.add(
                        egui::Button::new(text)
                            .fill(if selected { p.primary } else { Color32::TRANSPARENT })
                            .corner_radius(CornerRadius::same(15))
                            .min_size(vec2(0.0, 30.0)),
                    );
                    if r.clicked() && !selected {
                        *value = *option;
                        changed = true;
                    }
                }
            });
        },
    );
    changed
}

/// One button of the dock under the device: icon over a small label.
pub fn dock_button(
    ui: &mut Ui,
    p: &Palette,
    icon: &str,
    label: &str,
    enabled: bool,
    tint: Option<Color32>,
) -> Response {
    let size = vec2(72.0, 54.0);
    let (rect, response) = ui.allocate_exact_size(size, if enabled { Sense::click() } else { Sense::hover() });
    let alpha = if enabled { 1.0 } else { 0.38 };
    if response.hovered() && enabled {
        ui.painter().rect_filled(rect, CornerRadius::same(14), p.container_highest);
    }
    let color = tint.unwrap_or(p.on_surface).gamma_multiply(alpha);
    ui.painter().text(
        rect.center() - vec2(0.0, 8.0),
        Align2::CENTER_CENTER,
        icon,
        FontId::new(22.0, theme::icons()),
        color,
    );
    ui.painter().text(
        rect.center() + vec2(0.0, 15.0),
        Align2::CENTER_CENTER,
        label,
        FontId::new(11.5, theme::medium()),
        tint.unwrap_or(p.on_surface_variant).gamma_multiply(alpha),
    );
    if enabled { response.on_hover_cursor(egui::CursorIcon::PointingHand) } else { response }
}

/// A rounded progress bar; indeterminate (a moving segment) when `fraction` is `None`.
pub fn progress(ui: &mut Ui, p: &Palette, fraction: Option<f32>, width: f32) {
    let (rect, _) = ui.allocate_exact_size(vec2(width, 6.0), Sense::hover());
    let painter = ui.painter();
    painter.rect_filled(rect, CornerRadius::same(3), p.container_highest);
    match fraction {
        Some(f) => {
            let mut fill = rect;
            fill.set_width((rect.width() * f.clamp(0.0, 1.0)).max(6.0));
            painter.rect_filled(fill, CornerRadius::same(3), p.primary);
        }
        None => {
            let t = (ui.input(|i| i.time) * 0.8).fract() as f32;
            let seg = rect.width() * 0.3;
            let x0 = rect.left() + (rect.width() + seg) * t - seg;
            let fill = egui::Rect::from_min_max(
                egui::pos2(x0.max(rect.left()), rect.top()),
                egui::pos2((x0 + seg).min(rect.right()), rect.bottom()),
            );
            if fill.width() > 0.0 {
                painter.rect_filled(fill, CornerRadius::same(3), p.primary);
            }
            ui.ctx().request_repaint();
        }
    }
}

/// A round busy indicator in the primary colour.
pub fn spinner(ui: &mut Ui, p: &Palette, size: f32) {
    ui.add(egui::Spinner::new().size(size).color(p.primary));
}

/// A row in a settings card: icon, title, subtitle, and a trailing control.
pub fn setting_row<R>(
    ui: &mut Ui,
    p: &Palette,
    icon: &str,
    title: &str,
    subtitle: &str,
    trailing: impl FnOnce(&mut Ui) -> R,
) -> R {
    ui.horizontal(|ui| {
        ui.set_min_height(48.0);
        ui.add_space(2.0);
        ui.label(icon_text(icon, 20.0).color(p.on_surface_variant));
        ui.add_space(6.0);
        ui.vertical(|ui| {
            ui.add_space(if subtitle.is_empty() { 14.0 } else { 4.0 });
            ui.label(RichText::new(title).size(14.5));
            if !subtitle.is_empty() {
                ui.label(RichText::new(subtitle).size(12.5).color(p.on_surface_variant));
            }
        });
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), trailing).inner
    })
    .inner
}

pub fn muted(p: &Palette, text: impl Into<String>) -> RichText {
    RichText::new(text.into()).color(p.on_surface_variant)
}

/// An icon followed by a label, each in its own font (Inter's private-use glyphs overlap Phosphor's codepoints, so
/// the two never share a text run). `color` of `None` follows the widget's text colour.
pub fn icon_label(icon: &str, text: &str, color: Option<Color32>) -> egui::text::LayoutJob {
    let mut job = egui::text::LayoutJob::default();
    let format = |font: FontId| egui::TextFormat {
        font_id: font,
        color: color.unwrap_or(Color32::PLACEHOLDER),
        valign: egui::Align::Center,
        ..Default::default()
    };
    job.append(icon, 0.0, format(FontId::new(16.0, theme::icons())));
    job.append(text, 10.0, format(FontId::new(14.0, egui::FontFamily::Proportional)));
    job
}
