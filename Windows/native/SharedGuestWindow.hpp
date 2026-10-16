// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "../../Desktop/native/include/Window.h"
#include "qemu-ios-ui.h"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace lt::windows {
// Host owns recipe/admission, the true-main QEMU initializer, cooperative stop,
// exclusive running.lock and durable dirty/log proof. This class only connects
// the existing shared SDL window to the public coherent-frame/input API.
struct Controls {
    std::function<bool(bool)> pause;
    std::function<void()> stop;
    std::function<void()> rotate;
    std::function<void(const void *, int, int, int)> screenshot;
    std::function<void(const std::string &)> error;
};

class SharedGuestWindow {
    LTDesktopWindow *window_ = nullptr;
    Controls controls_;
    std::vector<unsigned char> pixels_;
    QemuIosPortableFrame frame_{};
    uint64_t serial_ = 0, submission_ = 0, timestamp_ = 0;
    bool stopping_ = false, faulted_ = false, enabled_ = false;
    static constexpr size_t max_frame = 64u * 1024u * 1024u;

    void error(const std::string &message) { if (controls_.error) controls_.error(message); }
    void begin_stop() {
        if (stopping_) return;
        stopping_ = true;
        lt_desktop_window_enabled(window_, 0);
        qemu_ios_portable_cancel_input();
        if (controls_.stop) controls_.stop();
    }
    void fail(const std::string &message) {
        error(message);
        faulted_ = true;
        begin_stop();
    }
    void events() {
        LTDesktopEvent event{};
        for (int budget = 0; budget < 512; ++budget) {
            const int result = lt_desktop_window_poll(window_, &event);
            if (result < 0) { fail(lt_desktop_window_error()); return; }
            if (result == 0) return;
            switch (event.kind) {
            case LT_DESKTOP_BUTTON:
                // The shared window emits balanced releases before Stop/Pause.
                qemu_ios_ui_button(event.identifier, event.phase != 0);
                break;
            case LT_DESKTOP_TOUCH: {
                QemuIosPortableContact contact{};
                contact.size = sizeof(contact); contact.version = QEMU_IOS_PORTABLE_VERSION;
                contact.contact_id = event.identifier; contact.phase = event.phase;
                contact.x = event.x; contact.y = event.y;
                const auto now = std::chrono::steady_clock::now().time_since_epoch();
                timestamp_ = std::max(timestamp_ + 1, static_cast<uint64_t>(
                    std::chrono::duration_cast<std::chrono::nanoseconds>(now).count()));
                const int queued = qemu_ios_portable_touch(++submission_, timestamp_, &contact, 1);
                if (queued != QEMU_IOS_QUEUED) error("Guest touch admission failed: " + std::to_string(queued));
                break;
            }
            case LT_DESKTOP_PAUSE:
                if (!stopping_ && enabled_) {
                    const bool paused = !qemu_ios_ui_paused();
                    qemu_ios_portable_cancel_input();
                    if (!controls_.pause || !controls_.pause(paused)) error("Pause/resume request was refused.");
                }
                break;
            case LT_DESKTOP_STOP: begin_stop(); break;
            case LT_DESKTOP_ROTATE:
                // Shared SDL already rotated presentation and touch mapping.
                // A guest orientation notification is an optional extra.
                if (!stopping_ && controls_.rotate) controls_.rotate();
                break;
            case LT_DESKTOP_SCREENSHOT:
                if (!pixels_.empty() && frame_.serial == serial_ && serial_ && controls_.screenshot)
                    controls_.screenshot(pixels_.data(), frame_.width, frame_.height, frame_.stride);
                else error("No guest frame is available for a host screenshot.");
                break;
            case LT_DESKTOP_VISIBLE:
                // Desktop visibility must not call the upstream iOS foreground
                // API: that API changes VM runstate on hide/show. Lifecycle
                // pause stays explicit; lost-focus input is already released.
                if (!event.phase) qemu_ios_portable_cancel_input();
                break;
            default: fail("Unknown shared guest-window event."); return;
            }
        }
    }
public:
    explicit SharedGuestWindow(const char *title, Controls controls) : controls_(std::move(controls)) {
        window_ = lt_desktop_window_create(title, 480, 800);
        if (!window_) throw std::runtime_error(lt_desktop_window_error());
    }
    SharedGuestWindow(const SharedGuestWindow &) = delete;
    SharedGuestWindow &operator=(const SharedGuestWindow &) = delete;
    ~SharedGuestWindow() {
        if (window_) {
            lt_desktop_window_cancel(window_);
            qemu_ios_portable_cancel_input();
            lt_desktop_window_destroy(window_);
        }
        // No process destruction, forced shutdown, lease release or clean mark.
    }
    void tick() {
        if (faulted_) return;
        enabled_ = qemu_ios_ui_ready() && !stopping_;
        lt_desktop_window_enabled(window_, enabled_);
        lt_desktop_window_paused(window_, qemu_ios_ui_paused());
        events();
        if (faulted_) return;
        QemuIosPortableFrame next{};
        next.size = sizeof(next); next.version = QEMU_IOS_PORTABLE_VERSION;
        int result = qemu_ios_portable_frame_copy(pixels_.empty() ? nullptr : pixels_.data(), pixels_.size(), serial_, &next);
        if (result == QEMU_IOS_BUFFER_SMALL) {
            if (!next.required_bytes || next.required_bytes > max_frame) {
                fail("Guest frame exceeds the 64 MiB presentation bound."); return;
            }
            try { pixels_.resize(next.required_bytes); }
            catch (const std::bad_alloc &) { fail("Cannot allocate guest presentation buffer."); return; }
            result = qemu_ios_portable_frame_copy(pixels_.data(), pixels_.size(), serial_, &next);
        }
        if (result == QEMU_IOS_OK) {
            if (!next.width || !next.height || next.width > 4096 || next.height > 4096 ||
                next.format != 1 || next.stride != next.width * 4 ||
                next.required_bytes != static_cast<uint64_t>(next.stride) * next.height ||
                next.required_bytes > pixels_.size()) {
                fail("Invalid coherent guest-frame metadata."); return;
            }
            if (lt_desktop_window_frame(window_, pixels_.data(), next.width, next.height, next.stride) != 0) {
                fail(lt_desktop_window_error()); return;
            }
            frame_ = next; serial_ = next.serial;
        } else if (result != QEMU_IOS_NO_FRAME && result != QEMU_IOS_NOT_READY && result != QEMU_IOS_BUFFER_SMALL) {
            fail("Guest frame copy failed: " + std::to_string(result)); return;
        }
        events(); // A frame size change may have generated balanced releases.
        if (!faulted_ && lt_desktop_window_present(window_, qemu_ios_ui_paused(), stopping_) != 0)
            fail(lt_desktop_window_error());
    }
    bool stopping() const { return stopping_; }
    bool failed() const { return faulted_; }
};
}
