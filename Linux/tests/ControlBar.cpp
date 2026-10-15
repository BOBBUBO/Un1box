#include "../ControlBar.hpp"
#include <cassert>
#include <cmath>
#include <iostream>
#include <tuple>
#include <vector>

int main(int argc, char **argv) {
    using A = lt::WindowAction;
    const lt::ControlBarLayout layout{480, 800};
    assert(layout.top() == 712);
    for (int rotation : {0, 90, 180, 270}) {
        const auto guest = layout.guest(320, 480, rotation);
        assert(guest.width > 0 && guest.height > 0);
        assert(guest.left >= 0 && guest.top >= 0);
        assert(guest.top + guest.height <= layout.top());
        const double w = rotation % 180 ? 480 : 320;
        const double h = rotation % 180 ? 320 : 480;
        assert(std::abs(guest.width / guest.height - w / h) < 1e-8);
        // Toolbar coordinates always fall outside the mapped guest rectangle.
        for (int i = 0; i < 8; ++i) {
            const auto r = layout.rect(i);
            assert(layout.hit(r.x + r.w / 2, r.y + r.h / 2) == A(i));
            assert(r.y >= guest.top + guest.height);
        }
    }
    assert(layout.hit(10, 711) == A::none);
    assert(layout.hit(-1, 730) == A::none);
    assert(layout.hit(480, 730) == A::none);
    assert(layout.hit(10, 800) == A::none);
    assert((lt::ControlBarLayout{480, 70}.guest(320, 480, 0).scale == 0));
    lt::WindowControls controls;
    using Owner = lt::WindowControls::Owner;
    const Owner mouse{0, 1}, finger{1, 1}, keyboard{2, 1};
    std::vector<std::pair<int, bool>> edges;
    std::vector<A> actions;
    auto edge = [&](int b, bool d) { edges.emplace_back(b, d); return true; };
    auto activate = [&](A a) { actions.push_back(a); };
    controls.press(mouse, A::power, edge);
    controls.press(finger, A::power, edge);
    controls.press(keyboard, A::power, edge);
    controls.press(mouse, A::home, edge); // No duplicate-down/reassignment.
    assert(edges.size() == 1 && edges[0] == std::make_pair(1, true));
    controls.release(mouse, A::power, edge, activate);
    controls.release(finger, A::power, edge, activate);
    assert(edges.size() == 1);
    controls.release(keyboard, A::none, edge, activate);
    assert(edges.size() == 2 && edges[1] == std::make_pair(1, false));
    assert(!controls.any() && actions.empty());
    controls.press(mouse, A::home, edge);
    controls.press(finger, A::power, edge);
    assert(!controls.matches(mouse, A::power));
    controls.leave(mouse, edge);
    assert(controls.owns(mouse)); // Drag outside remains consumed until release.
    assert(!controls.active(A::home));
    assert(edges.back() == std::make_pair(0, false));
    const auto after_leave = edges.size();
    controls.release(mouse, A::power, edge, activate);
    assert(edges.size() == after_leave && actions.empty());
    controls.release(finger, A::power, edge, activate);
    controls.press(mouse, A::pause, edge);
    controls.leave(mouse, edge);
    controls.release(mouse, A::pause, edge, activate);
    assert(actions.empty());
    controls.press(mouse, A::pause, edge);
    controls.leave(mouse, edge);
    controls.release(mouse, A::none, edge, activate);
    assert(actions.empty()); // Consumed drag release on guest is not an action.
    controls.press(mouse, A::rotate, edge);
    controls.release(mouse, A::rotate, edge, activate);
    assert(actions.size() == 1 && actions.back() == A::rotate);
    controls.press(finger, A::screenshot, edge);
    controls.release(finger, A::stop, edge, activate);
    assert(actions.size() == 1); // No action if released on another control.
    controls.press(mouse, A::power, edge);
    controls.clear(); // Real focus loss/resize uses universal worker Cancel.
    const auto after_cancel = edges.size();
    controls.release(mouse, A::power, edge, activate);
    assert(edges.size() == after_cancel && !controls.any());
    // Backpressure callback may clear all owners while press/release runs.
    controls.press(mouse, A::home, [&](int, bool) { controls.clear(); return false; });
    assert(!controls.any());
    controls.press(mouse, A::power, edge);
    controls.release(mouse, A::power, [&](int, bool) { controls.clear(); return false; }, activate);
    assert(!controls.any());

    for (A action : {A::home, A::power, A::quieter, A::louder}) {
        const auto before = edges.size();
        controls.press(finger, action, edge);
        controls.release(finger, action, edge, activate);
        assert(edges.size() == before + 2);
        assert(edges[before] == std::make_pair(lt::WindowControls::button(action), true));
        assert(edges[before + 1] == std::make_pair(lt::WindowControls::button(action), false));
    }
    for (A action : {A::pause, A::rotate, A::screenshot, A::stop}) {
        const auto before = actions.size();
        controls.press(finger, action, edge);
        controls.release(finger, action, edge, activate);
        assert(actions.size() == before + 1 && actions.back() == action);
    }

    assert(SDL_Init(0) == 0);
    auto *surface = SDL_CreateRGBSurfaceWithFormat(0, 960, 1600, 32, SDL_PIXELFORMAT_ARGB8888);
    assert(surface);
    auto *renderer = SDL_CreateSoftwareRenderer(surface);
    assert(renderer);
    assert(SDL_RenderSetScale(renderer, 2, 2) == 0);
    SDL_SetRenderDrawColor(renderer, 100, 70, 30, 255);
    SDL_RenderClear(renderer);
    controls.press(mouse, A::power, edge);
    lt::draw_control_bar(renderer, layout, controls, false, false);
    SDL_RenderPresent(renderer);
    auto pixel = [&](int x, int y) {
        return static_cast<uint32_t *>(surface->pixels)[y * surface->pitch / 4 + x];
    };
    assert(pixel(20, 1400) == SDL_MapRGBA(surface->format, 100, 70, 30, 255));
    assert(pixel(20, 1500) != pixel(20, 1400));
    assert(pixel(260, 1440) != pixel(20, 1440)); // Pressed Power highlighted.
    if (argc == 2)
        assert(SDL_SaveBMP(surface, argv[1]) == 0);
    SDL_DestroyRenderer(renderer);
    SDL_FreeSurface(surface);
    SDL_Quit();
    std::cout << "controlbar geometry, pointer/keyboard ownership, balanced edges, cancellation and 2x render PASS; host only\n";
}
