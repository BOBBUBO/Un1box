#include "../SharedFrameRead.hpp"
#include <algorithm>
#include <cassert>
#include <iostream>

int main() {
    std::vector<uint8_t> mapping(sizeof(lt_shared_frame) + 256);
    auto *shared = reinterpret_cast<lt_shared_frame *>(mapping.data());
    shared->size = sizeof(*shared);
    shared->version = 1;
    shared->capacity = LT_FRAME_CAPACITY;
    std::vector<uint8_t> pixels;
    lt_frame_info output{};
    uint64_t proof = 777;
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::again);
    assert(pixels.empty() && proof == 777 && output.sequence == 0);

    lt_frame_info f{sizeof(f), 1, 4, 3, 16, 1, 12, 123456, 48};
    std::vector<uint8_t> source(size_t(f.bytes), 0x5a);
    assert(lt::publish_frame(shared, f, source.data(), source.size()));
    const auto published_generation = shared->generation;
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::copied);
    assert(output.sequence == 12 && output.timestamp_ns == 123456 && proof == published_generation);
    assert(!(proof & 1) && pixels.size() == f.bytes && pixels == source);
    // A static sequence must still be freshly copied, without a new publication.
    pixels.assign(pixels.size(), 0);
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::copied);
    assert(pixels == source && proof == published_generation);

    const auto previous = output;
    proof = 999;
    unsigned reads = 0;
    assert(lt::screenshot_read(shared, pixels, output, proof, [&](unsigned) {
               ++reads;
               ++f.sequence;
               std::fill(source.begin(), source.end(), uint8_t(f.sequence));
               assert(lt::publish_frame(shared, f, source.data(), source.size()));
           }) == lt::ScreenshotRead::again);
    assert(reads == 3 && proof == 999 && output.sequence == previous.sequence);
    // A single conflicting publication retries the actual copy and metadata.
    reads = 0;
    assert(lt::screenshot_read(shared, pixels, output, proof, [&](unsigned attempt) {
               ++reads;
               if (!attempt) {
                   ++f.sequence;
                   std::fill(source.begin(), source.end(), 0x71);
                   assert(lt::publish_frame(shared, f, source.data(), source.size()));
               }
           }) == lt::ScreenshotRead::copied);
    assert(reads == 2 && pixels == source && output.sequence == f.sequence && proof == shared->generation);
    assert(!(proof & 1));

    // Odd/incomplete metadata is never diagnosed as an oversized stable frame.
    __atomic_add_fetch(&shared->generation, 1, __ATOMIC_SEQ_CST);
    auto invalid = f;
    invalid.bytes = LT_FRAME_CAPACITY + 1;
    lt::store_frame_metadata(shared->frame, invalid);
    const auto accepted = output;
    const auto accepted_proof = proof;
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::again);
    assert(output.sequence == accepted.sequence && proof == accepted_proof);
    __atomic_add_fetch(&shared->generation, 1, __ATOMIC_SEQ_CST);
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::invalid);
    assert(output.sequence == accepted.sequence && proof == accepted_proof);
    shared->capacity = 0;
    assert(lt::screenshot_read(shared, pixels, output, proof) == lt::ScreenshotRead::invalid);
    std::cout << "coherent current screenshot production helper PASS (host only)\n";
}
