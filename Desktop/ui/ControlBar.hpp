#pragma once
#include "Commands.hpp"
#include <algorithm>
#include <array>
#include <cstdint>
#include <map>
#include <utility>

namespace lt {
struct GuestViewport {
    double left = 0, top = 0, width = 0, height = 0, scale = 0;
};
struct ControlBarLayout {
    static constexpr int height = 88;
    int width, window_height;
    int top() const { return std::max(0, window_height - height); }
    SDL_Rect rect(int index) const {
        const int column = index % 4, row = index / 4;
        const int row_height = (window_height - top()) / 2;
        return {column * width / 4, top() + row * row_height,
                (column + 1) * width / 4 - column * width / 4, row_height};
    }
    WindowAction hit(double x, double y) const {
        if (width <= 0 || x < 0 || x >= width || y < top() || y >= window_height)
            return WindowAction::none;
        for (int i = 0; i < 8; ++i) {
            const auto r = rect(i);
            if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h)
                return desktop_commands[i].action;
        }
        return WindowAction::none;
    }
    GuestViewport guest(int guest_width, int guest_height, int rotation) const {
        if (guest_width <= 0 || guest_height <= 0 || width <= 0 || top() <= 0)
            return {};
        const double w = rotation % 180 ? guest_height : guest_width;
        const double h = rotation % 180 ? guest_width : guest_height;
        const double scale = std::min(width / w, top() / h);
        return {(width - w * scale) / 2, (top() - h * scale) / 2, w * scale, h * scale, scale};
    }
};
// Pointer identities have separate namespaces for mouse, fingers and keyboard.
// Multiple sources holding one hardware button produce only one down/up pair.
class WindowControls {
  public:
    using Owner = std::pair<int, int64_t>;
  private:
    std::map<Owner, WindowAction> held;
  public:
    static int button(WindowAction action) {
        return desktop_button(action);
    }
    bool active(WindowAction action) const {
        for (const auto &item : held)
            if (item.second == action)
                return true;
        return false;
    }
    bool owns(Owner owner) const { return held.count(owner); }
    bool matches(Owner owner, WindowAction action) const {
        const auto item = held.find(owner);
        return item != held.end() && item->second == action;
    }
    bool any() const { return !held.empty(); }
    void clear() { held.clear(); } // Used with the worker's universal Cancel.
    template <class Edge> void press(Owner owner, WindowAction action, Edge edge) {
        if (action == WindowAction::none || owns(owner))
            return;
        const bool already = active(action);
        held.emplace(owner, action);
        if (!already && button(action) >= 0)
            edge(button(action), true);
    }
    template <class Edge, class Activate>
    void release(Owner owner, WindowAction under_pointer, Edge edge, Activate activate) {
        auto found = held.find(owner);
        if (found == held.end())
            return;
        const auto action = found->second;
        held.erase(found); // Callbacks may cancel and clear all remaining owners.
        if (button(action) >= 0) {
            if (!active(action))
                edge(button(action), false);
        } else if (action != WindowAction::none && action == under_pointer)
            activate(action);
    }
    template <class Edge> void leave(Owner owner, Edge edge) {
        auto found = held.find(owner);
        if (found == held.end() || found->second == WindowAction::none)
            return;
        const auto action = found->second;
        found->second = WindowAction::none; // Consume until up; never become guest input.
        if (button(action) >= 0 && !active(action))
            edge(button(action), false);
    }
};
// Tiny built-in uppercase bitmap labels avoid an external font/runtime dependency.
inline std::array<uint8_t, 5> control_glyph(char ch) {
    static constexpr std::array<std::array<uint8_t, 5>, 26> alphabet{{
        {126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},
        {127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},
        {0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},{127,64,64,64,64},
        {127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},{127,9,9,9,6},
        {62,65,81,33,94},{127,9,25,41,70},{70,73,73,73,49},{1,1,127,1,1},
        {63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},{99,20,8,20,99},
        {7,8,112,8,7},{97,81,73,69,67}}};
    if (ch >= 'A' && ch <= 'Z')
        return alphabet[ch - 'A'];
    if (ch == '+') return {8,8,62,8,8};
    if (ch == '-') return {8,8,8,8,8};
    return {};
}
inline void control_text(SDL_Renderer *renderer, const char *text, SDL_Rect rect, int y, int scale) {
    int count = 0;
    while (text[count]) ++count;
    int x = rect.x + (rect.w - (count * 6 - 1) * scale) / 2;
    for (int i = 0; i < count; ++i) {
        const auto glyph = control_glyph(text[i]);
        for (int col = 0; col < 5; ++col)
            for (int row = 0; row < 7; ++row)
                if (glyph[col] & (1 << row)) {
                    SDL_Rect dot{x + col * scale, y + row * scale, scale, scale};
                    SDL_RenderFillRect(renderer, &dot);
                }
        x += 6 * scale;
    }
}
inline void draw_control_bar(SDL_Renderer *renderer, ControlBarLayout layout, const WindowControls &controls,
                             bool paused, bool stopping) {
    SDL_SetRenderDrawColor(renderer, 18, 20, 25, 255);
    SDL_Rect background{0, layout.top(), layout.width, layout.window_height - layout.top()};
    SDL_RenderFillRect(renderer, &background);
    for (int i = 0; i < 8; ++i) {
        auto rect = layout.rect(i);
        rect.x += 3; rect.y += 3; rect.w -= 6; rect.h -= 6;
        const bool active = controls.active(desktop_commands[i].action);
        SDL_SetRenderDrawColor(renderer, active ? 43 : 40, active ? 106 : 44, active ? 151 : 54, 255);
        SDL_RenderFillRect(renderer, &rect);
        SDL_SetRenderDrawColor(renderer, stopping ? 120 : 235, stopping ? 125 : 239, stopping ? 130 : 245, 255);
        const int scale = rect.w >= 108 && rect.h >= 32 ? 2 : 1;
        const auto &command = desktop_commands[i];
        const char *label = command.action == WindowAction::pause && paused ? "RESUME" : command.label;
        control_text(renderer, label, rect, rect.y + 4, scale);
        SDL_SetRenderDrawColor(renderer, 163, 177, 195, 255);
        control_text(renderer, command.shortcut, rect, rect.y + rect.h - 10, 1);
    }
}
} // namespace lt
