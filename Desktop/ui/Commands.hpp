#pragma once
#include <SDL.h>
#include <array>
#include <string>

namespace lt {
// Stable within the desktop UI, not a wire/native engine ABI.
enum class WindowAction { home, power, quieter, louder, pause, rotate, screenshot, stop, none, cancel };
struct DesktopCommand {
    WindowAction action;
    const char *label;
    const char *shortcut;
    SDL_Keycode key;
    int hardware_button; // Native contract: Home0, Power1, VolumeUp2, VolumeDown3.
};
// Order is the shared toolbar layout. All desktop adapters use this definition.
inline constexpr std::array<DesktopCommand, 8> desktop_commands{{
    {WindowAction::home, "HOME", "H", SDLK_h, 0},
    {WindowAction::power, "POWER", "P", SDLK_p, 1},
    {WindowAction::quieter, "VOL -", "-", SDLK_MINUS, 3},
    {WindowAction::louder, "VOL +", "+", SDLK_EQUALS, 2},
    {WindowAction::pause, "PAUSE", "SPACE", SDLK_SPACE, -1},
    {WindowAction::rotate, "ROTATE", "R", SDLK_r, -1},
    {WindowAction::screenshot, "HOST SHOT", "S", SDLK_s, -1},
    {WindowAction::stop, "STOP", "ESC", SDLK_ESCAPE, -1},
}};
inline WindowAction desktop_shortcut(SDL_Keycode key) {
    if (key == SDLK_c)
        return WindowAction::cancel;
    for (const auto &command : desktop_commands)
        if (command.key == key)
            return command.action;
    return WindowAction::none;
}
inline int desktop_button(WindowAction action) {
    for (const auto &command : desktop_commands)
        if (command.action == action)
            return command.hardware_button;
    return -1;
}
inline std::string desktop_shortcut_help() {
    std::string result;
    for (const auto &command : desktop_commands) {
        if (!result.empty()) result += ", ";
        result += std::string(command.shortcut) + ": " + command.label;
    }
    return result + ". C: release all contacts/buttons.";
}
} // namespace lt
