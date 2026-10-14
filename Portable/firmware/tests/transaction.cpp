#include "internal.hpp"
#include <cassert>
#include <iostream>
#include <unistd.h>
using namespace ltf;
static int cancel(void *, const char *, uint64_t, uint64_t) { return 1; }
int main() {
    char tmp[] = "/tmp/lighttouch-fw-test-XXXXXX";
    assert(mkdtemp(tmp));
    fs::path work(tmp);
    write(work / "stock", bytes("synthetic wrong IPSW"));
    Json entry = {{"id", "n72ap-7E18"},
                  {"board", "n72ap"},
                  {"build", "7E18"},
                  {"version", "3.1.3"},
                  {"recipe", {{"system_mib", 7168}}},
                  {"source", {{"sha1", "5f4f5c01eda2f811f73167e7d1f82dbeed82367b"}, {"bytes", 263275211}}}};
    write(work / "catalog", bytes(Json{{"entries", Json::array({entry})}}.dump()));
    std::string catalog = (work / "catalog").string(), stock = (work / "stock").string();
    std::string dest = (work / "new-output").string();
    lt_fw_request r{};
    r.size = sizeof(r);
    r.version = 1;
    r.catalog_path = catalog.c_str();
    r.firmware_id = "n72ap-7E18";
    r.ipsw_path = stock.c_str();
    r.guest_tools_path = tmp;
    r.guest_inventory_path = "unneeded";
    r.destination = dest.c_str();
    lt_fw_error error{};
    error.size = sizeof(error);
    error.version = 1;
    assert(lt_firmware_prepare(&r, &error) == LT_FW_CHECKSUM);
    assert(!fs::exists(dest));
    assert(read(work / "stock") == bytes("synthetic wrong IPSW"));
    r.progress = cancel;
    assert(lt_firmware_prepare(&r, &error) == LT_FW_CANCELLED);
    assert(!fs::exists(dest));
    r.progress = nullptr;
    r.firmware_id = "n90ap-8A293";
    assert(lt_firmware_prepare(&r, &error) == LT_FW_UNSUPPORTED);
    r.version = 2;
    assert(lt_firmware_prepare(&r, &error) == LT_FW_ARGUMENT);
    fs::remove_all(work);
    std::cout << "checksum, cancellation, sources unchanged, unsupported recipe, ABI gates passed\n";
}
