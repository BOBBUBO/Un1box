// Asset-free semantic adapter test. Supplies a fake engine and window ABI;
// exercises real adapter logic, no SDL renderer or guest qualification.
#include "SharedGuestWindow.hpp"
#include <cassert>
#include <cstring>
#include <deque>
#include <iostream>

struct LTDesktopWindow {};
static LTDesktopWindow fake_window;
static std::deque<LTDesktopEvent> pending;
static bool ready = true, paused = false, bad_frame = false, frame_available = true;
static int buttons = 0, touches = 0, cancels = 0, presents = 0, frames = 0;
static uint64_t last_timestamp = 0;
extern "C" {
LTDesktopWindow *lt_desktop_window_create(const char *, int, int) { return &fake_window; }
const char *lt_desktop_window_error() { return "synthetic window failure"; }
void lt_desktop_window_destroy(LTDesktopWindow *) {}
int lt_desktop_window_frame(LTDesktopWindow *, const void *p, int w, int h, int stride) {
    assert(p && w == 2 && h == 2 && stride == 8); ++frames; return 0;
}
int lt_desktop_window_present(LTDesktopWindow *, int, int) { ++presents; return 0; }
int lt_desktop_window_poll(LTDesktopWindow *, LTDesktopEvent *event) {
    if (pending.empty()) return 0;
    *event = pending.front(); pending.pop_front(); return 1;
}
void lt_desktop_window_cancel(LTDesktopWindow *) {}
void lt_desktop_window_enabled(LTDesktopWindow *, int) {}
void lt_desktop_window_paused(LTDesktopWindow *, int) {}
bool qemu_ios_ui_ready() { return ready; }
bool qemu_ios_ui_paused() { return paused; }
void qemu_ios_ui_button(int button, bool down) { assert(button == 0 || button == 1 || button == 2 || button == 3); buttons += down ? 1 : -1; }
int qemu_ios_portable_cancel_input() { ++cancels; return QEMU_IOS_QUEUED; }
int qemu_ios_portable_touch(uint64_t id, uint64_t timestamp, const QemuIosPortableContact *contact, size_t count) {
    assert(id && timestamp > last_timestamp && count == 1 && contact->version == 1);
    last_timestamp = timestamp; ++touches; return QEMU_IOS_QUEUED;
}
int qemu_ios_portable_frame_copy(void *pixels, size_t capacity, uint64_t serial, QemuIosPortableFrame *out) {
    assert(out->size == sizeof(*out) && out->version == 1);
    if (!frame_available || serial == 7) return QEMU_IOS_NO_FRAME;
    out->width = 2; out->height = 2; out->stride = 8; out->format = 1; out->serial = 7;
    out->required_bytes = bad_frame ? 65u * 1024u * 1024u : 16;
    if (!pixels || capacity < out->required_bytes) return QEMU_IOS_BUFFER_SMALL;
    std::memset(pixels, 17, 16); return QEMU_IOS_OK;
}
}

int main() {
    int stops = 0, screenshots = 0, errors = 0, rotations = 0;
    lt::windows::Controls controls;
    controls.pause = [&](bool value) { paused = value; return true; };
    controls.stop = [&] { ++stops; };
    controls.rotate = [&] { ++rotations; };
    controls.error = [&](const std::string &) { ++errors; };
    controls.screenshot = [&](const void *pixels, int width, int height, int stride) {
        assert(pixels && width == 2 && height == 2 && stride == 8); ++screenshots;
    };
    {
        lt::windows::SharedGuestWindow window("shared", controls);
        window.tick(); assert(frames == 1 && presents == 1 && !window.failed());
        pending = {{LT_DESKTOP_BUTTON, 0, 1, 0, 0}, {LT_DESKTOP_BUTTON, 0, 0, 0, 0},
                   {LT_DESKTOP_TOUCH, 0, 0, .2, .5}, {LT_DESKTOP_TOUCH, 1, 0, .8, .5},
                   {LT_DESKTOP_TOUCH, 0, 2, .2, .5}, {LT_DESKTOP_TOUCH, 1, 2, .8, .5},
                   {LT_DESKTOP_SCREENSHOT, 0, 0, 0, 0}, {LT_DESKTOP_PAUSE, 0, 0, 0, 0}};
        window.tick();
        assert(buttons == 0 && touches == 4 && screenshots == 1 && paused && frames == 1);
        pending = {{LT_DESKTOP_VISIBLE, 0, 0, 0, 0}};
        window.tick(); assert(paused); // Hide never silently resumes a paused VM.
        pending = {{LT_DESKTOP_PAUSE, 0, 0, 0, 0}, {LT_DESKTOP_ROTATE, 0, 0, 0, 0}};
        window.tick(); assert(!paused && rotations == 1);
        pending = {{LT_DESKTOP_STOP, 0, 0, 0, 0}, {LT_DESKTOP_STOP, 0, 0, 0, 0}};
        window.tick(); assert(window.stopping() && stops == 1 && errors == 0);
    }
    assert(stops == 1); // Window destruction never stops/disposes another process.
    {
        bad_frame = true;
        lt::windows::SharedGuestWindow window("bounded", controls);
        window.tick(); assert(window.failed() && stops == 2 && errors == 1);
    }
    {
        bad_frame = false; frame_available = false;
        lt::windows::SharedGuestWindow window("no frame", controls);
        pending = {{LT_DESKTOP_SCREENSHOT, 0, 0, 0, 0}};
        window.tick(); assert(!window.failed() && errors == 2 && screenshots == 1);
    }
    {
        // Native SDL presentation does not require a guest orientation hook.
        controls.rotate = {};
        lt::windows::SharedGuestWindow window("host rotation only", controls);
        pending = {{LT_DESKTOP_ROTATE, 0, 0, 0, 0}};
        window.tick(); assert(!window.failed() && errors == 2 && rotations == 1);
    }
    assert(cancels > 0);
    std::cout << "shared Windows adapter: coherent frame bound, multi-contact routing, buttons, pause/visibility/Stop, screenshot and fault retention PASS; synthetic host only\n";
}
