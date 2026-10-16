#pragma once
#include <SDL.h>

namespace lt {
// iOS has absolute touch input, not a guest mouse pointer. Keep the desktop
// arrow visible over both the guest and its controls, without trapping it.
inline void desktop_cursor(SDL_Window *window) {
    SDL_SetRelativeMouseMode(SDL_FALSE);
    if (window)
        SDL_SetWindowGrab(window, SDL_FALSE);
    SDL_SetCursor(SDL_GetDefaultCursor());
    SDL_ShowCursor(SDL_ENABLE);
}
inline void desktop_cursor_event(SDL_Window *window, const SDL_Event &event) {
    if (event.type == SDL_WINDOWEVENT &&
        (event.window.event == SDL_WINDOWEVENT_ENTER ||
         event.window.event == SDL_WINDOWEVENT_FOCUS_GAINED))
        desktop_cursor(window);
}
} // namespace lt
