// Asset-free adapter test. Dummy SDL video is used only to supply window
// geometry; production create must reject its unavailable hardware renderer.
#include "../native/Window.cpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <vector>

int main() {
    assert(SDL_setenv("SDL_VIDEODRIVER", "dummy", 1) == 0);
    assert(SDL_InitSubSystem(SDL_INIT_VIDEO | SDL_INIT_EVENTS) == 0);
    LTDesktopWindow window;
    window.window = SDL_CreateWindow("synthetic adapter geometry", 0, 0, 480, 800, SDL_WINDOW_HIDDEN);
    assert(window.window);
    window.guest_width = 320; window.guest_height = 480; window.enabled = true;
    auto take = [&] {
        std::vector<LTDesktopEvent> events(window.pending.begin(), window.pending.end());
        window.pending.clear(); return events;
    };
    auto key = [&](SDL_Keycode code, bool down) {
        SDL_Event event{};
        event.type = down ? SDL_KEYDOWN : SDL_KEYUP;
        event.key.keysym.sym = code;
        window.dispatch(event);
    };
    const auto home = window.layout().rect(0);
    const double hx = home.x + home.w / 2, hy = home.y + home.h / 2;
    window.pointer({0, 0}, 0, hx, hy);
    key(SDLK_h, true);
    auto events = take();
    assert(events.size() == 1 && events[0].kind == LT_DESKTOP_BUTTON && events[0].phase == 1);
    window.pointer({0, 0}, 2, hx, hy);
    assert(take().empty()); // Keyboard still owns Home.
    key(SDLK_h, false);
    events = take();
    assert(events.size() == 1 && events[0].identifier == 0 && events[0].phase == 0);

    const auto view = window.layout().guest(320, 480, 0);
    const double left = view.left + .2 * view.width, right = view.left + .8 * view.width;
    const double y = view.top + .5 * view.height;
    window.pointer({1, 11}, 0, left, y);
    window.pointer({1, 22}, 0, right, y);
    events = take();
    assert(events.size() == 2 && events[0].identifier == 0 && events[1].identifier == 1);
    window.pointer({1, 11}, 1, right, y);
    window.pointer({1, 22}, 1, left, y);
    events = take();
    assert(events.size() == 2 && events[0].identifier == 0 && events[1].identifier == 1);
    assert(std::abs(events[0].x - .8) < 1e-8 && std::abs(events[1].x - .2) < 1e-8);
    window.pointer({1, 11}, 2, right, y);
    events = take();
    assert(events.size() == 1 && events[0].identifier == 0 && events[0].phase == 2);
    assert(window.contacts.size() == 1);
    key(SDLK_p, true);
    take();
    SDL_Event focus{}; focus.type = SDL_WINDOWEVENT; focus.window.event = SDL_WINDOWEVENT_FOCUS_LOST;
    window.dispatch(focus);
    events = take();
    assert(events.size() == 2 && events[0].kind == LT_DESKTOP_BUTTON && events[0].phase == 0);
    assert(events[1].kind == LT_DESKTOP_TOUCH && events[1].identifier == 1 && events[1].phase == 2);
    key(SDLK_p, false);
    window.pointer({1, 22}, 2, left, y);
    assert(take().empty());

    // Toolbar drag-off cannot turn into a guest contact or fire a command.
    window.pointer({0, 0}, 0, hx, hy);
    take();
    window.pointer({0, 0}, 1, left, y);
    events = take(); assert(events.size() == 1 && events[0].phase == 0);
    window.pointer({0, 0}, 2, left, y);
    assert(take().empty() && window.contacts.empty());

    for (int finger = 0; finger < 11; ++finger) window.pointer({1, finger}, 0, left, y);
    assert(window.contacts.size() == 10 && take().size() == 10);
    // Pause releases every contact before delivering the action.
    key(SDLK_SPACE, true); key(SDLK_SPACE, false);
    events = take(); assert(events.size() == 11);
    for (int i = 0; i < 10; ++i) assert(events[i].kind == LT_DESKTOP_TOUCH && events[i].phase == 2);
    assert(events.back().kind == LT_DESKTOP_PAUSE && window.contacts.empty());

    SDL_Event synthetic{}; synthetic.type = SDL_FINGERDOWN;
    synthetic.tfinger.touchId = SDL_MOUSE_TOUCHID; synthetic.tfinger.x = .5; synthetic.tfinger.y = .5;
    window.dispatch(synthetic);
    assert(take().empty());
    window.pointer({1, 100}, 0, left, y); take();
    window.pointer({1, 100}, 1, hx, hy);
    events = take();
    assert(events.size() == 1 && events[0].kind == LT_DESKTOP_TOUCH && events[0].phase == 2);
    window.pointer({1, 100}, 1, right, y);
    assert(take().empty()); // Contact cannot be re-created without a fresh down.

    key(SDLK_ESCAPE, true); key(SDLK_ESCAPE, false);
    events = take(); assert(events.size() == 1 && events[0].kind == LT_DESKTOP_STOP);
    assert(!window.enabled && window.stopping);
    lt_desktop_window_enabled(&window, 1);
    assert(!window.enabled); // Closing does not silently restart a session.
    key(SDLK_h, true); assert(take().empty());
    assert(lt_desktop_window_frame(&window, nullptr, 320, 480, 1280) == -1);
    assert(lt_desktop_window_poll(nullptr, nullptr) == -1);
    SDL_DestroyWindow(window.window);
    SDL_QuitSubSystem(SDL_INIT_VIDEO | SDL_INIT_EVENTS);
    assert(!lt_desktop_window_create("requires hardware", 480, 800));
    assert(!active_window);
    assert(std::string(lt_desktop_window_error()).find("hardware") != std::string::npos);
    std::cout << "native shared adapter ownership/multitouch/cancel/stop/hardware refusal PASS; synthetic host only\n";
}
