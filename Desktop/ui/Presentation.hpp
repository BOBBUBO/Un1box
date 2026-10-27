#pragma once
#include <SDL.h>
namespace lt {
inline SDL_Texture *opaque_texture(SDL_Renderer *renderer, int width, int height) {
    // Guest display BGRA alpha is unused and can be zero. Treat its fourth byte
    // as padding without changing copied/shared/native screenshot bytes.
    SDL_Texture *texture =
        SDL_CreateTexture(renderer, SDL_PIXELFORMAT_BGRX32, SDL_TEXTUREACCESS_STREAMING, width, height);
    if (texture && SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_NONE) != 0) {
        SDL_DestroyTexture(texture);
        return nullptr;
    }
    return texture;
}
} // namespace lt
