#include "internal.hpp"
extern "C" {
extern char endianness;
int lt_fw_guard_hfs(int, char **);
}
int main(int argc, char **argv) {
    short n = 1;
    endianness = *(char *)&n ? 1 : 0;
    if (argc == 4 && std::string(argv[2]) == "portable-grow") {
        try {
            auto size = std::stoull(argv[3]);
            ltf::require(size % 4096 == 0 && size / 4096 <= 1835008, "test growth geometry");
            ltf::grow_hfsx(argv[1], size / 4096);
            return 0;
        } catch (...) {
            return 1;
        }
    }
    return lt_fw_guard_hfs(argc, argv);
}
