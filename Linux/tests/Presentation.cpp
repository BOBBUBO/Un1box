#include "../Presentation.hpp"
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
int main() {
    assert(SDL_Init(0) == 0);
    auto *surface = SDL_CreateRGBSurfaceWithFormat(0, 80, 80, 32, SDL_PIXELFORMAT_ARGB8888);
    assert(surface);
    auto *renderer = SDL_CreateSoftwareRenderer(surface);
    assert(renderer);
    auto *texture = lt::opaque_texture(renderer, 2, 3);
    assert(texture);
    // A real software render of unused alpha=0 display pixels must remain lit
    // after rotation. This is renderer regression evidence, not a guest pass.
    const std::array<uint8_t, 24> bgra = {0, 0,   255, 0, 0,   255, 0, 0, 255, 0, 0,   0,
                                          0, 255, 255, 0, 255, 255, 0, 0, 255, 0, 255, 0};
    assert(SDL_UpdateTexture(texture, nullptr, bgra.data(), 8) == 0);
    SDL_Rect dest{30, 20, 20, 30};
    std::array<uint8_t, 80 * 80 * 4> rendered{};
    auto read = [&](int rotation) {
        assert(SDL_SetRenderDrawColor(renderer, 24, 24, 24, 255) == 0);
        assert(SDL_RenderClear(renderer) == 0);
        assert(SDL_RenderCopyEx(renderer, texture, nullptr, &dest, rotation, nullptr, SDL_FLIP_NONE) == 0);
        assert(SDL_RenderReadPixels(renderer, nullptr, SDL_PIXELFORMAT_RGBA32, rendered.data(), 80 * 4) == 0);
    };
    auto color = [&](int x, int y, uint8_t r, uint8_t g, uint8_t b) {
        auto p = rendered.data() + (y * 80 + x) * 4;
        assert(p[0] == r && p[1] == g && p[2] == b);
    };
    read(0);
    color(35, 25, 255, 0, 0);
    color(45, 45, 255, 0, 255);
    read(90);
    color(50, 30, 255, 0, 0);
    color(30, 40, 255, 0, 255);
    read(180);
    color(45, 45, 255, 0, 0);
    read(270);
    color(30, 40, 255, 0, 0);
    assert(bgra[3] == 0 && bgra[23] == 0);
    SDL_DestroyTexture(texture);
    SDL_DestroyRenderer(renderer);
    SDL_FreeSurface(surface);
    SDL_Quit();
    std::cout << "opaque zero-alpha software rotation PASS; host renderer only\n";
}
