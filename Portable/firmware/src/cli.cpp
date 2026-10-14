#include "lighttouch_firmware.h"
#include <cstdio>
static int progress(void *, const char *s, uint64_t d, uint64_t t) {
    if (!d || d == t)
        fprintf(stderr, "%s %llu/%llu\n", s, (unsigned long long)d, (unsigned long long)t);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 7) {
        fprintf(stderr, "usage: lighttouch-prepare CATALOG ID STOCK_IPSW GUEST_TOOLS GUEST_MANIFEST NEW_DESTINATION\n");
        return 2;
    }
    lt_fw_request r{};
    r.size = sizeof(r);
    r.version = LT_FIRMWARE_ABI;
    r.catalog_path = argv[1];
    r.firmware_id = argv[2];
    r.ipsw_path = argv[3];
    r.guest_tools_path = argv[4];
    r.guest_manifest_path = argv[5];
    r.destination = argv[6];
    r.progress = progress;
    lt_fw_error e{};
    e.size = sizeof(e);
    e.version = LT_FIRMWARE_ABI;
    auto status = lt_firmware_prepare(&r, &e);
    fprintf(stderr, "status=%d %s\n", status, e.message);
    return status ? 1 : 0;
}
