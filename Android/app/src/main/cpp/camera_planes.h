// One YUV_420_888 image into the NV12 frame the emulated ISP draws.
//
// Android hands a camera frame as three planes with a row stride (bytes between
// rows) and a pixel stride (bytes between samples) that are the camera's, not
// the picture's: phones pad rows for alignment, and some deliver chroma
// semi-planar (two planes interleaved, pixel stride 2) while others deliver it
// planar (pixel stride 1). All three shapes are legal in one frame's description,
// so all three are handled here rather than assumed.
//
// The emulated device wants exactly one NV12 frame: luma at full size, then
// interleaved Cb/Cr at half size in both directions. This is a pass over the
// picture -- a few hundred microseconds for a preview frame on a phone -- done on
// the camera's own thread, once per frame it hands over.
#ifndef UN1BOX_CAMERA_PLANES_H
#define UN1BOX_CAMERA_PLANES_H

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace camera_planes {

using std::ptrdiff_t;

struct Plane {
    const uint8_t *data;
    int row_stride;     // bytes from the start of one row to the next
    int pixel_stride;   // bytes from one sample to the next
};

// Copies the luma plane, honouring both strides.
inline void luma(const Plane &plane, int width, int height, uint8_t *out, int out_stride) {
    for (int y = 0; y < height; ++y) {
        const uint8_t *row = plane.data + ptrdiff_t(y) * plane.row_stride;
        uint8_t *target = out + ptrdiff_t(y) * out_stride;
        if (plane.pixel_stride == 1) {
            std::memcpy(target, row, size_t(width));
        } else {
            for (int x = 0; x < width; ++x) target[x] = row[ptrdiff_t(x) * plane.pixel_stride];
        }
    }
}

// Copies the two chroma planes into one interleaved Cb/Cr half-size plane. A
// planar frame (pixel stride 1) has one chroma sample per two pixels; a
// semi-planar one (pixel stride 2) has the sample at every other byte of its own
// plane, and the two planes may even be views of one buffer, which is why each
// sample is read by its own stride rather than by arithmetic on the other plane.
inline void chroma(const Plane &cb, const Plane &cr, int width, int height, uint8_t *out,
                   int out_stride) {
    const int rows = height / 2, columns = width / 2;
    for (int y = 0; y < rows; ++y) {
        const uint8_t *cb_row = cb.data + ptrdiff_t(y) * cb.row_stride;
        const uint8_t *cr_row = cr.data + ptrdiff_t(y) * cr.row_stride;
        uint8_t *target = out + ptrdiff_t(y) * out_stride;
        for (int x = 0; x < columns; ++x) {
            const ptrdiff_t index = ptrdiff_t(x) * cb.pixel_stride;
            target[ptrdiff_t(x) * 2] = cb_row[index];
            target[ptrdiff_t(x) * 2 + 1] = cr_row[ptrdiff_t(x) * cr.pixel_stride];
        }
    }
}

// The whole frame. `out` must hold width * height * 3 / 2 bytes.
inline bool to_nv12(const Plane &y, const Plane &cb, const Plane &cr, int width, int height,
                    uint8_t *out) {
    if (!y.data || !cb.data || !cr.data || !out) return false;
    if (width < 2 || height < 2 || (width & 1) || (height & 1)) return false;
    if (y.row_stride < width || cb.row_stride < width / 2 || cr.row_stride < width / 2) return false;
    if (y.pixel_stride < 1 || cb.pixel_stride < 1 || cr.pixel_stride < 1) return false;
    luma(y, width, height, out, width);
    chroma(cb, cr, width, height, out + ptrdiff_t(width) * height, width);
    return true;
}

}  // namespace camera_planes

#endif
