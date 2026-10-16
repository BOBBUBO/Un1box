// SPDX-License-Identifier: GPL-2.0-or-later
// Windows main-thread adapter for the maintained SDL guest display. This
// executable is an internal worker, not another launcher or asset importer.
#define SDL_MAIN_HANDLED
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <io.h>
#include <fcntl.h>
#include <SDL.h>
#include "SharedGuestWindow.hpp"
#include "engine.h"
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace {
std::wstring wide(const std::string &text) {
    const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.c_str(), -1, nullptr, 0);
    if (!count) throw std::runtime_error("Invalid UTF-8 Windows path.");
    std::wstring result(count, L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.c_str(), -1, result.data(), count))
        throw std::runtime_error("Cannot decode Windows path.");
    result.resize(count - 1); return result;
}
struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    Handle() = default;
    explicit Handle(HANDLE v) : value(v) {}
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
    ~Handle() { if (value != INVALID_HANDLE_VALUE && value != nullptr) CloseHandle(value); }
};
void error(const std::string &message) { std::fprintf(stderr, "LightTouch: %s\n", message.c_str()); }
void regular(HANDLE handle, bool single_link) {
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(handle, &info) || GetFileType(handle) != FILE_TYPE_DISK ||
        (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) ||
        (single_link && info.nNumberOfLinks != 1))
        throw std::runtime_error("Worker state requires regular files without reparse points or hard links.");
}
void write_all(HANDLE handle, const char *text) {
    size_t remaining = std::strlen(text);
    while (remaining) {
        DWORD done = 0;
        if (!WriteFile(handle, text, static_cast<DWORD>(remaining), &done, nullptr) || !done)
            throw std::runtime_error("Cannot write worker shutdown marker.");
        remaining -= done; text += done;
    }
    if (!FlushFileBuffers(handle)) throw std::runtime_error("Cannot flush worker shutdown marker.");
}
class StateLease {
    Handle directory_, lease_;
    fs::path dirty_;
public:
    StateLease(const fs::path &directory, bool recover) {
        directory_.value = CreateFileW(directory.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        BY_HANDLE_FILE_INFORMATION info{};
        if (directory_.value == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(directory_.value, &info) ||
            !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT))
            throw std::runtime_error("Choose an admitted private device-state directory, without a junction or symlink.");
        // SAME preview lease name. Sharing=0 blocks C# and shared workers.
        lease_.value = CreateFileW((directory/L"running.lock").c_str(), GENERIC_READ | GENERIC_WRITE,
            0, nullptr, OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (lease_.value == INVALID_HANDLE_VALUE)
            throw std::runtime_error("This saved device is already in use, or its state lock cannot be opened.");
        regular(lease_.value, true);
        dirty_ = directory/L"shutdown-unconfirmed.json";
        const DWORD attrs = GetFileAttributesW(dirty_.c_str());
        if (attrs != INVALID_FILE_ATTRIBUTES) {
            if (attrs & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))
                throw std::runtime_error("Shutdown marker must be a regular private file.");
            if (!recover) throw std::runtime_error("Shutdown is unconfirmed. Explicitly select recovery to preserve and cold-boot this state.");
        } else if (GetLastError() != ERROR_FILE_NOT_FOUND) {
            throw std::runtime_error("Cannot inspect the saved device shutdown marker.");
        }
        Handle dirty(CreateFileW(dirty_.c_str(), GENERIC_WRITE, 0, nullptr,
            attrs == INVALID_FILE_ATTRIBUTES ? CREATE_NEW : OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (dirty.value == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot create the private shutdown marker.");
        regular(dirty.value, true);
        if (!SetFilePointerEx(dirty.value, {}, nullptr, FILE_BEGIN) || !SetEndOfFile(dirty.value))
            throw std::runtime_error("Cannot prepare the shutdown marker.");
        write_all(dirty.value, "{\"reason\":\"shared Windows worker has not confirmed guest shutdown\"}\n");
    }
    bool mark_clean() {
        if (DeleteFileW(dirty_.c_str())) return true;
        error("Guest stopped, but the unconfirmed marker could not be removed. State remains unconfirmed.");
        return false;
    }
};

class ControlPipe {
    HANDLE input_ = GetStdHandle(STD_INPUT_HANDLE);
    std::string pending_;
    bool closed_ = false;
public:
    void poll() {
        if (closed_) return;
        DWORD available = 0;
        if (!PeekNamedPipe(input_, nullptr, 0, nullptr, &available, nullptr)) {
            closed_ = true;
            if (GetLastError() == ERROR_BROKEN_PIPE) {
                error("Launcher control pipe closed; cooperative shutdown requested, state retained until confirmed.");
                lt_windows_engine_stop();
            } else {
                error("No launcher control pipe; use Stop in the guest window.");
            }
            return;
        }
        if (!available) return;
        char bytes[1024]; DWORD read = 0;
        if (!ReadFile(input_, bytes, std::min<DWORD>(available, sizeof(bytes)), &read, nullptr)) {
            error("Launcher pipe read failed; cooperative shutdown requested.");
            closed_ = true; lt_windows_engine_stop(); return;
        }
        pending_.append(bytes, read);
        for (size_t position; (position = pending_.find('\n')) != std::string::npos;) {
            const std::string line = pending_.substr(0, position);
            pending_.erase(0, position + 1);
            if (line == "stop" || line == "stop\r") {
                if (!lt_windows_engine_stop()) error("Stop refused; guest/storage state and lease retained.");
            } else error("Unknown launcher control command.");
        }
        if (pending_.size() > 4096) {
            error("Launcher command exceeds the input bound; cooperative shutdown requested.");
            closed_ = true; lt_windows_engine_stop();
        }
    }
};
void screenshot(const fs::path &directory, const void *pixels, int width, int height, int stride) {
    if (directory.empty()) { error("No screenshot folder was admitted for this session."); return; }
    FILETIME time{}; GetSystemTimePreciseAsFileTime(&time);
    ULARGE_INTEGER stamp{}; stamp.LowPart = time.dwLowDateTime; stamp.HighPart = time.dwHighDateTime;
    const fs::path path = directory/(L"LightTouch-" + std::to_wstring(stamp.QuadPart) + L".bmp");
    Handle file(CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (file.value == INVALID_HANDLE_VALUE) { error("Cannot create host screenshot in the admitted screenshot folder."); return; }
    const int fd = _open_osfhandle(reinterpret_cast<intptr_t>(file.value), _O_WRONLY | _O_BINARY);
    if (fd < 0) { error("Cannot open screenshot stream."); return; }
    file.value = INVALID_HANDLE_VALUE; // Ownership transferred to CRT.
    FILE *stream = _fdopen(fd, "wb");
    if (!stream) { _close(fd); error("Cannot create screenshot writer."); return; }
    SDL_Surface *surface = SDL_CreateRGBSurfaceWithFormatFrom(const_cast<void *>(pixels), width, height, 32, stride, SDL_PIXELFORMAT_BGRA32);
    SDL_RWops *rw = SDL_RWFromFP(stream, SDL_TRUE);
    if (!surface || !rw) {
        if (surface) SDL_FreeSurface(surface);
        if (rw) SDL_RWclose(rw); else std::fclose(stream);
        error("Cannot encode host screenshot."); return;
    }
    const int result = SDL_SaveBMP_RW(surface, rw, 1);
    SDL_FreeSurface(surface);
    if (result) error(SDL_GetError()); else std::fprintf(stderr, "[host-screenshot] %s\n", path.u8string().c_str());
}
}

static int run(int argc, char **argv) {
    try {
        std::string title = "LightTouch";
        fs::path state, screenshots;
        bool recover = false;
        int index = 1;
        for (; index < argc && std::string(argv[index]) != "--"; ++index) {
            const std::string option = argv[index];
            if (option == "--recover") { recover = true; continue; }
            if (index + 1 >= argc) throw std::runtime_error("Missing worker option value.");
            const std::string value = argv[++index];
            if (option == "--title") title = value;
            else if (option == "--state") state = fs::path(wide(value));
            else if (option == "--screenshots") screenshots = fs::path(wide(value));
            else throw std::runtime_error("Unknown Windows worker option.");
        }
        if (index == argc || state.empty() || !state.is_absolute())
            throw std::runtime_error("Internal worker requires --state <admitted-private-state> -- <literal admitted QEMU arguments>.");
        std::vector<char *> engine_args{argv[0]};
        bool display_none = false, no_shutdown = false;
        for (++index; index < argc; ++index) {
            engine_args.push_back(argv[index]);
            if (std::string(argv[index]) == "-display" && index + 1 < argc && std::string(argv[index + 1]) == "none") display_none = true;
            if (std::string(argv[index]) == "-no-shutdown") no_shutdown = true;
        }
        if (!display_none || !no_shutdown) throw std::runtime_error("Worker requires -display none and -no-shutdown.");
        StateLease lease(state, recover);
        lt::windows::Controls controls;
        controls.pause = [](bool pause) { return lt_windows_engine_pause(pause); };
        controls.stop = [] { if (!lt_windows_engine_stop()) error("Stop refused; writable state remains retained."); };
        controls.error = error;
        controls.screenshot = [&](const void *pixels, int w, int h, int stride) { screenshot(screenshots, pixels, w, h, stride); };
        // Actual main thread owns QEMU init/constructor registration and SDL.
        // Fail graphics creation BEFORE initializing an engine with live threads.
        SDL_SetMainReady();
        lt::windows::SharedGuestWindow window(title.c_str(), std::move(controls));
        const int engine_argc = static_cast<int>(engine_args.size());
        engine_args.push_back(nullptr);
        lt_windows_engine_initialize(engine_argc, engine_args.data());
        lt_windows_engine_start();
        ControlPipe input;
        bool frontend_failed = false;
        while (lt_windows_engine_running()) {
            // Never unwind the lease while initialized engine threads live.
            // UI errors request cooperative Stop and keep pumping its pipe.
            try {
                input.poll();
                if (!frontend_failed) window.tick();
            } catch (const std::exception &failure) {
                frontend_failed = true;
                error(failure.what());
                lt_windows_engine_stop();
            }
            SDL_Delay(8);
        }
        const int engine_exit = lt_windows_engine_join();
        const bool clean = engine_exit == 0 && !frontend_failed && !window.failed() && lease.mark_clean();
        // Internal engine proof is separate from the sole full session FINAL.
        std::printf("FINAL status_available=1 state=0 confirmed=%d dirty=%d worker_reaped=1 worker_exit=%d worker_disposal_requested=0 frontend_return=%d\n",
                    engine_exit == 0, !clean, engine_exit, clean ? 0 : 3);
        return clean ? 0 : 3;
    } catch (const std::exception &failure) {
        error(failure.what());
        return 3;
    }
}

// MinGW narrow main argv otherwise follows the system ANSI codepage. Convert
// the native command line ourselves so QEMU and the shared UTF-8 ABI receive
// exact non-ASCII device/package/state names on every Windows locale.
int main() {
    int argc = 0;
    wchar_t **arguments = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!arguments) { error("Cannot read the native Windows command line."); return 3; }
    std::vector<std::string> values;
    bool valid = true;
    for (int index = 0; index < argc; ++index) {
        const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, arguments[index], -1, nullptr, 0, nullptr, nullptr);
        if (!count) { valid = false; break; }
        std::string value(count, '\0');
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, arguments[index], -1, value.data(), count, nullptr, nullptr)) { valid = false; break; }
        value.resize(count - 1); values.push_back(std::move(value));
    }
    LocalFree(arguments);
    if (!valid) { error("Windows command line contains invalid Unicode."); return 3; }
    std::vector<char *> argv;
    for (auto &value : values) argv.push_back(value.data());
    argv.push_back(nullptr);
    return run(argc, argv.data());
}
