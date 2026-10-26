// NV12 out of an Android YUV_420_888 image, for the ISP's camera tap.
//
// Camera2 does not hand out a packed frame: the three planes come with a row stride (the device's own,
// often wider than the picture) and a pixel stride (1 for planar Y and for the U/V of a planar layout, 2
// for a semi-planar one, where U and V interleave inside each other's buffer). Getting this wrong is the
// classic way a phone camera shows sheared or striped garbage, so the conversion lives here, in plain C++
// with no JNI in it, and is checked by tests/camera/jni-glue.cpp on both layouts.
//
// It is written the way the API allows rather than the way one device happens to lay a frame out: every
// sample is read at (row * rowStride + column * pixelStride), and only the Y rows that exist are read.
#ifndef LIGHTTOUCH_CAMERA_PLANES_H
#define LIGHTTOUCH_CAMERA_PLANES_H

#include <cstddef>
#include <cstdint>

namespace lt {

// The frame's size in an NV12 buffer: a Y plane, then interleaved CbCr at half the resolution.
inline size_t nv12Bytes(int width, int height) {
    return size_t(width) * size_t(height) * 3 / 2;
}

// True when these strides and this size describe a frame that can be read without going outside a plane.
// `planeRows` is the number of rows the caller's buffer really has (the image's height for Y, height/2 for
// chroma); a device that reports a shorter buffer is refused rather than read past.
inline bool cameraPlanesFit(int width, int height, int yRow, int yPixel, int uRow, int uPixel, int vRow,
                            int vPixel, size_t yBytes, size_t uBytes, size_t vBytes) {
    if (width <= 0 || height <= 0 || (width & 1) || (height & 1)) return false;
    if (yPixel < 1 || uPixel < 1 || vPixel < 1) return false;
    const size_t chromaWidth = size_t(width) / 2, chromaHeight = size_t(height) / 2;
    const size_t yNeed = size_t(yRow) * size_t(height - 1) + size_t(yPixel) * size_t(width - 1) + 1;
    const size_t uNeed = size_t(uRow) * (chromaHeight - 1) + size_t(uPixel) * (chromaWidth - 1) + 1;
    const size_t vNeed = size_t(vRow) * (chromaHeight - 1) + size_t(vPixel) * (chromaWidth - 1) + 1;
    if (yRow < int(size_t(yPixel) * size_t(width))) return false;
    if (uRow < int(size_t(uPixel) * chromaWidth) || vRow < int(size_t(vPixel) * chromaWidth)) return false;
    return yNeed <= yBytes && uNeed <= uBytes && vNeed <= vBytes;
}

// One frame into `out` (nv12Bytes(width, height) bytes): Y per pixel, then for each 2x2 block its Cb and Cr.
// The caller has already checked the planes with cameraPlanesFit.
inline void cameraToNv12(const uint8_t *y, int yRow, int yPixel, const uint8_t *u, int uRow, int uPixel,
                         const uint8_t *v, int vRow, int vPixel, int width, int height, uint8_t *out) {
    uint8_t *uv = out + size_t(width) * size_t(height);
    for (int row = 0; row < height; row++) {
        const uint8_t *src = y + size_t(row) * size_t(yRow);
        uint8_t *dst = out + size_t(row) * size_t(width);
        if (yPixel == 1) {
            for (int x = 0; x < width; x++) dst[x] = src[x];
        } else {
            for (int x = 0; x < width; x++) dst[x] = src[size_t(x) * size_t(yPixel)];
        }
    }
    for (int row = 0; row < height / 2; row++) {
        const uint8_t *cb = u + size_t(row) * size_t(uRow);
        const uint8_t *cr = v + size_t(row) * size_t(vRow);
        uint8_t *dst = uv + size_t(row) * size_t(width);
        for (int x = 0; x < width / 2; x++) {
            dst[size_t(x) * 2] = cb[size_t(x) * size_t(uPixel)];
            dst[size_t(x) * 2 + 1] = cr[size_t(x) * size_t(vPixel)];
        }
    }
}

}  // namespace lt

#endif
