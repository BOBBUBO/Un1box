// One YUV_420_888 image into NV12, against the real Android/app/src/main/cpp/camera_planes.h.
//
// Three shapes of the same frame are checked, because all three come off real phones: planar chroma
// (pixel stride 1), semi-planar chroma (pixel stride 2, the two planes often views of one buffer), and rows
// padded by the camera (row stride longer than the picture). A conversion that assumes any one of them shows
// green stripes or a rolled picture on the phones that send another.
#include "camera_planes.h"

#include <cstddef>
#include <cstdio>
#include <cstring>
#include <vector>

static int failures;
static constexpr int W = 8, H = 4;

static void check(bool ok, const char *what) {
    if (!ok) {
        std::printf("FAIL %s\n", what);
        failures++;
    }
}

// Luma is a ramp, chroma a pair of constants, so a wrong row or a swapped Cb/Cr is visible in one comparison.
static uint8_t luma_sample(int x, int y) { return uint8_t(16 + y * 16 + x); }
static constexpr uint8_t CB = 90, CR = 170;

static void compare(const std::vector<uint8_t> &out, const char *what) {
    for (int y = 0; y < H; ++y) {
        for (int x = 0; x < W; ++x) {
            check(out[size_t(y) * W + x] == luma_sample(x, y), what);
        }
    }
    const uint8_t *uv = out.data() + std::ptrdiff_t(W) * H;
    for (int i = 0; i < (W * H) / 2; i += 2) {
        check(uv[i] == CB && uv[i + 1] == CR, what);
    }
}

// Planar: three separate tightly packed planes.
static void planar() {
    std::vector<uint8_t> y(size_t(W) * H), u(size_t(W) * H / 4), v(size_t(W) * H / 4), out(size_t(W) * H * 3 / 2);
    for (int row = 0; row < H; ++row)
        for (int x = 0; x < W; ++x) y[size_t(row) * W + x] = luma_sample(x, row);
    std::fill(u.begin(), u.end(), CB);
    std::fill(v.begin(), v.end(), CR);
    const camera_planes::Plane py{y.data(), W, 1}, pc{u.data(), W / 2, 1}, pcr{v.data(), W / 2, 1};
    check(camera_planes::to_nv12(py, pc, pcr, W, H, out.data()), "planar frame accepted");
    compare(out, "planar");
}

// Semi-planar: Cb and Cr interleaved in one buffer, which is what most phones deliver. The two plane pointers
// are one byte apart in the same allocation -- reading either one by the other's offset is the bug this catches.
static void semi_planar() {
    std::vector<uint8_t> y(size_t(W) * H), chroma(size_t(W) * H / 2), out(size_t(W) * H * 3 / 2);
    for (int row = 0; row < H; ++row)
        for (int x = 0; x < W; ++x) y[size_t(row) * W + x] = luma_sample(x, row);
    for (size_t i = 0; i < chroma.size(); i += 2) { chroma[i] = CB; chroma[i + 1] = CR; }
    const camera_planes::Plane py{y.data(), W, 1};
    const camera_planes::Plane pc{chroma.data(), W, 2}, pcr{chroma.data() + 1, W, 2};
    check(camera_planes::to_nv12(py, pc, pcr, W, H, out.data()), "semi-planar frame accepted");
    compare(out, "semi-planar");
}

// Padded rows: the camera's row stride is longer than the picture, as hardware alignment makes it.
static void padded() {
    constexpr int LUMA_ROW = W + 6, CHROMA_ROW = W + 2;
    std::vector<uint8_t> y(size_t(LUMA_ROW) * H, 0xee), u(size_t(CHROMA_ROW) * (H / 2), 0xee),
        v(size_t(CHROMA_ROW) * (H / 2), 0xee), out(size_t(W) * H * 3 / 2);
    for (int row = 0; row < H; ++row)
        for (int x = 0; x < W; ++x) y[size_t(row) * LUMA_ROW + x] = luma_sample(x, row);
    std::fill(u.begin(), u.end(), CB);
    std::fill(v.begin(), v.end(), CR);
    const camera_planes::Plane py{y.data(), LUMA_ROW, 1}, pc{u.data(), CHROMA_ROW, 1}, pcr{v.data(), CHROMA_ROW, 1};
    check(camera_planes::to_nv12(py, pc, pcr, W, H, out.data()), "padded frame accepted");
    compare(out, "padded");
}

static void refuses_what_it_cannot_convert() {
    std::vector<uint8_t> y(64), u(16), v(16), out(96);
    const camera_planes::Plane py{y.data(), W, 1}, pc{u.data(), W / 2, 1}, pcr{v.data(), W / 2, 1};
    check(!camera_planes::to_nv12(py, pc, pcr, W - 1, H, out.data()), "an odd width is refused");
    check(!camera_planes::to_nv12(py, pc, pcr, W, H - 1, out.data()), "an odd height is refused");
    check(!camera_planes::to_nv12(py, pc, pcr, 0, H, out.data()), "an empty frame is refused");
    const camera_planes::Plane narrow{y.data(), W - 2, 1};
    check(!camera_planes::to_nv12(narrow, pc, pcr, W, H, out.data()), "a row shorter than the picture is refused");
}

int main() {
    planar();
    semi_planar();
    padded();
    refuses_what_it_cannot_convert();
    if (failures) {
        std::printf("%d camera plane checks failed\n", failures);
        return 1;
    }
    std::printf("planar, semi-planar and padded frames all become NV12; impossible shapes are refused\n");
    return 0;
}
