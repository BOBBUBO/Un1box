/* SPDX-License-Identifier: MIT
 * Native shared-library load/discovery only. Never enters qemu_ios_main.
 * clang -std=gnu11 -Wall -Wextra -Werror -I QEMU/contrib/ios-app \
 *   macOS/tests/EngineAbi.c -o engine-abi
 */
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include "qemu-ios-ui.h"

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: engine-abi /absolute/libqemu-arm.dylib\n");
        return 2;
    }
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "engine load failed: %s\n", dlerror()); return 1; }
    uint32_t (*version)(void) = dlsym(library, "qemu_ios_api_version");
    const QemuIosDeviceInfo *(*board_at)(int) = dlsym(library, "qemu_ios_device_info_at");
    bool (*ready)(void) = dlsym(library, "qemu_ios_ui_ready");
    assert(version && board_at && ready && dlsym(library, "qemu_ios_main"));
    assert((version() >> 16) == 2 && !ready());
    int found = 0, count = 0;
    for (; count < 128; ++count) {
        const QemuIosDeviceInfo *board = board_at(count);
        if (!board) break;
        assert(board->machine && board->board && board->screen_width > 0 && board->screen_height > 0);
        printf("board=%s machine=%s panel=%dx%d\n", board->board, board->machine,
               board->screen_width, board->screen_height);
        if (!strcmp(board->board, "n72ap")) found = 1;
    }
    assert(found && count > 0 && count < 128 && !ready());
    printf("PASS native engine ABI=0x%x eager load and %d-board discovery; no guest started\n", version(), count);
    /* Keep the QEMU library resident for this process's lifetime. */
    return 0;
}
