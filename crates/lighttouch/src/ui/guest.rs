//! The guest screen: the newest frame as a texture, and input. Left mouse is finger 1, right mouse finger 2, a
//! touchscreen's fingers map to their own contacts. A finger that leaves the screen is cancelled; a fresh press
//! starts a new gesture. Keys: H Home, P Sleep/Wake, +/- volume (held while the key is held).
use super::device_frame;
use eframe::egui::{self, Color32, Pos2, Rect, Sense, TextureHandle, TextureOptions, Ui};
use lighttouch_core::runtime::Contact;
use lighttouch_model::Model;
use lighttouch_model::settings::Scaling;
use std::collections::HashMap;

const DOWN: u32 = 0;
const MOVE: u32 = 1;
const UP: u32 = 2;
const CANCEL: u32 = 3;

#[derive(Default)]
pub struct GuestView {
    texture: Option<TextureHandle>,
    rgba: Vec<u8>,
    size: [usize; 2],
    scaling: Scaling,
    record: Option<String>,
    /// Mouse buttons holding a contact: (contact id, last position).
    mouse: HashMap<u32, Pos2>,
    /// Touchscreen finger id → contact id.
    touches: HashMap<u64, u32>,
    /// Hardware buttons held by the pointer on the drawn device or by keys.
    held: Vec<u32>,
    focused: bool,
}

impl GuestView {
    /// Pull a new frame into the texture. Returns true when one arrived.
    pub fn update(&mut self, ctx: &egui::Context, model: &mut Model) -> bool {
        let scaling = model.settings.scaling;
        let Some(session) = &mut model.session else {
            return false;
        };
        if self.record.as_deref() != Some(session.record_id.as_str()) {
            // A new session: drop the last device's picture.
            self.record = Some(session.record_id.clone());
            self.texture = None;
        }
        let Some(meta) = session.read_frame(&mut self.rgba) else {
            if scaling != self.scaling {
                self.scaling = scaling;
                if let Some(texture) = &mut self.texture {
                    let image = egui::ColorImage::from_rgba_unmultiplied(self.size, &self.rgba);
                    texture.set(image, options(scaling));
                }
            }
            return false;
        };
        self.size = [meta.width as usize, meta.height as usize];
        self.scaling = scaling;
        let image = egui::ColorImage::from_rgba_unmultiplied(self.size, &self.rgba);
        match &mut self.texture {
            Some(texture) => texture.set(image, options(scaling)),
            None => self.texture = Some(ctx.load_texture("guest", image, options(scaling))),
        }
        true
    }

    /// Forget the picture (the device stopped).
    pub fn clear(&mut self) {
        self.texture = None;
        self.record = None;
    }

    pub fn has_frame(&self) -> bool {
        self.texture.is_some()
    }

    /// Draw the screen into `rect` and route input to the guest when `interactive`.
    pub fn show(&mut self, ui: &mut Ui, rect: Rect, model: &mut Model, interactive: bool) -> egui::Response {
        let response = ui.interact(rect, ui.scope_id().with("guest-screen"), Sense::click_and_drag());
        ui.painter().rect_filled(rect, 0.0, Color32::BLACK);
        if let Some(texture) = &self.texture {
            let uv = Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0));
            ui.painter().image(texture.id(), rect, uv, Color32::WHITE);
        }
        if interactive {
            self.pointer_input(ui, rect, &response, model);
            self.keyboard_input(ui, model);
        } else {
            self.release_all(model);
        }
        response
    }

    fn pointer_input(&mut self, ui: &Ui, rect: Rect, response: &egui::Response, model: &mut Model) {
        let Some(session) = &mut model.session else { return };
        let normalized = |p: Pos2| Contact {
            id: 0,
            phase: MOVE,
            x: ((p.x - rect.left()) / rect.width()).clamp(0.0, 1.0) as f64,
            y: ((p.y - rect.top()) / rect.height()).clamp(0.0, 1.0) as f64,
        };
        let mut batch: Vec<Contact> = Vec::new();
        let (events, pointer) = ui.input(|i| (i.events.clone(), i.pointer.clone()));

        // Touchscreens: each finger is its own contact (ids 3..8); the mouse is ignored while fingers are down.
        for event in &events {
            if let egui::Event::Touch { id, phase, pos, .. } = event {
                let key = id.0;
                match phase {
                    egui::TouchPhase::Start if rect.contains(*pos) && self.touches.len() < 6 => {
                        let contact = (3..=8).find(|c| !self.touches.values().any(|v| v == c)).unwrap_or(8);
                        self.touches.insert(key, contact);
                        batch.push(Contact { id: contact, phase: DOWN, ..normalized(*pos) });
                    }
                    egui::TouchPhase::Move => {
                        if let Some(&contact) = self.touches.get(&key) {
                            if rect.contains(*pos) {
                                batch.push(Contact { id: contact, phase: MOVE, ..normalized(*pos) });
                            } else {
                                self.touches.remove(&key);
                                batch.push(Contact { id: contact, phase: CANCEL, ..normalized(*pos) });
                            }
                        }
                    }
                    egui::TouchPhase::End | egui::TouchPhase::Cancel => {
                        if let Some(contact) = self.touches.remove(&key) {
                            let phase = if *phase == egui::TouchPhase::End { UP } else { CANCEL };
                            batch.push(Contact { id: contact, phase, ..normalized(*pos) });
                        }
                    }
                    _ => {}
                }
            }
        }

        if self.touches.is_empty() {
            let position = pointer.interact_pos().or(pointer.latest_pos());
            for (button, contact) in [(egui::PointerButton::Primary, 1u32), (egui::PointerButton::Secondary, 2u32)] {
                let down = pointer.button_down(button);
                let held = self.mouse.get(&contact).copied();
                match (held, down, position) {
                    (None, true, Some(p))
                        if pointer.button_pressed(button) && response.hovered() && rect.contains(p) =>
                    {
                        self.mouse.insert(contact, p);
                        batch.push(Contact { id: contact, phase: DOWN, ..normalized(p) });
                    }
                    (Some(last), true, Some(p)) if p != last => {
                        if rect.contains(p) {
                            self.mouse.insert(contact, p);
                            batch.push(Contact { id: contact, phase: MOVE, ..normalized(p) });
                        } else {
                            // Leaving the screen ends ownership; coming back cannot continue it.
                            self.mouse.remove(&contact);
                            batch.push(Contact { id: contact, phase: CANCEL, ..normalized(last) });
                        }
                    }
                    (Some(last), false, _) => {
                        self.mouse.remove(&contact);
                        batch.push(Contact { id: contact, phase: UP, ..normalized(position.unwrap_or(last)) });
                    }
                    _ => {}
                }
            }
        }
        if !batch.is_empty() {
            batch.truncate(8);
            session.touch(batch);
        }
        // Losing focus releases everything (only on the change: some setups never report focus at all).
        let focused = ui.input(|i| i.focused);
        if self.focused && !focused {
            self.release_all(model);
        }
        self.focused = focused;
    }

    fn keyboard_input(&mut self, ui: &Ui, model: &mut Model) {
        if ui.ctx().egui_wants_keyboard_input() {
            return;
        }
        let keys = [
            (egui::Key::H, device_frame::HOME),
            (egui::Key::P, device_frame::POWER),
            (egui::Key::Plus, device_frame::VOLUME_UP),
            (egui::Key::Equals, device_frame::VOLUME_UP),
            (egui::Key::Minus, device_frame::VOLUME_DOWN),
        ];
        for (key, button) in keys {
            let down = ui.input(|i| i.key_down(key) && i.modifiers.is_none());
            let held = self.held.contains(&button);
            if down != held {
                self.set_button(model, button, down);
            }
        }
    }

    /// Press or release a hardware button (drawn device, fullscreen bar or keys).
    pub fn set_button(&mut self, model: &mut Model, button: u32, down: bool) {
        let held = self.held.contains(&button);
        if down == held {
            return;
        }
        if down {
            self.held.push(button);
        } else {
            self.held.retain(|b| *b != button);
        }
        if let Some(session) = &mut model.session {
            session.button(button, down);
        }
    }

    pub fn is_held(&self, button: u32) -> bool {
        self.held.contains(&button)
    }

    /// Release every contact and button (focus loss, the screen going away).
    pub fn release_all(&mut self, model: &mut Model) {
        if self.mouse.is_empty() && self.touches.is_empty() && self.held.is_empty() {
            return;
        }
        self.mouse.clear();
        self.touches.clear();
        self.held.clear();
        if let Some(session) = &mut model.session {
            session.cancel_input();
        }
    }
}

fn options(scaling: Scaling) -> TextureOptions {
    match scaling {
        Scaling::Smooth => TextureOptions::LINEAR,
        Scaling::Sharp => TextureOptions::NEAREST,
    }
}
