#include "internal.hpp"
#include <cassert>
#include <iostream>
#include <fstream>
#include <sstream>
using namespace ltf;
int main() {
    assert(lt_firmware_api_version() == 1);
    assert(page(0) == std::make_pair(3, 256));
    assert(page(1) == std::make_pair(0, 384));
    auto m = metadata(1835008, 4);
    assert(m.size() == 50);
    std::ifstream goldens(LT_METADATA_GOLDENS);
    assert(goldens);
    std::string line;
    size_t checked = 0;
    while (std::getline(goldens, line)) {
        if (line.empty() || line[0] == '#')
            continue;
        std::istringstream row(line);
        int cs, pg;
        std::string sha;
        row >> cs >> pg >> sha;
        assert(digest(m.at({cs, pg})) == sha);
        checked++;
    }
    assert(checked == m.size());

    assert(number(m.at({0, 4095 * 128}), 0x38, 4) == 0x43313134);
    assert(m.at({0, 256})[510] == 0x55);
    assert(number(m.at({1, 256}), 0x58, 4) != 0);
    Json j = {{"ProductVersion", "3.1.3"}, {"DeviceMap", Json::array({{{"BoardConfig", "n72ap"}, {"SCEP", 4}}})}};
    assert(plist(xml(j)) == j);
    assert(hex("abc") == Bytes({0x0a, 0xbc}));
    auto b = Bytes(16, 42), key = Bytes(16, 1), iv = Bytes(16);
    assert(aes(aes(b, key, iv, false), key, iv) == b);
    try {
        tags(Bytes(20));
        assert(false);
    } catch (const Error &) {
    }
    try {
        metadata(0, 4);
        assert(false);
    } catch (const Error &) {
    }
    lt_fw_error e{};
    e.size = sizeof(e);
    e.version = 1;
    assert(lt_firmware_prepare(nullptr, &e) == LT_FW_ARGUMENT);
    std::cout << "format vectors, geometry, malformed formats, version rejection passed\n";
}
