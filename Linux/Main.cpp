#include "lighttouch.h"
#include "../Portable/src/Elapsed.hpp"
#include "TouchScript.hpp"
#include "ControlDelivery.hpp"
#include "Presentation.hpp"
#include <SDL.h>
#include <sys/socket.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <time.h>
#include <array>
#include <algorithm>
#include <sstream>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <stdexcept>
#include <thread>
#include <vector>
namespace fs = std::filesystem;
static uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}
static void check(lt_status status, const lt_error &e) {
    if (status != LT_OK)
        throw std::runtime_error(e.message);
}
static void require(bool ok, const std::string &m) {
    if (!ok)
        throw std::runtime_error(m);
}
// Private inherited SOCK_SEQPACKET sockets: control/status and bounded PCM are
// separate channels, shared-frame bytes travel only through readonly memfd.
enum Kind : uint32_t {
    Hello = 1,
    Status = 2,
    Touch = 3,
    Button = 4,
    Pause = 5,
    Stop = 6,
    Cancel = 7,
    Motion = 8,
    Reply = 9,
    InputStatus = 10,
    GuestRequest = 11,
    GuestResult = 12,
    GuestCancel = 13,
    Screenshot = 14
};
struct Packet {
    uint32_t size = sizeof(Packet), version = 1, kind = 0, reserved = 0;
    lt_runtime_status status{};
    lt_runtime_diagnostics diagnostics{};
    lt_runtime_io_stats io{};
    lt_input_status input_status{};
    lt_touch_batch touch{};
    lt_motion motion{};
    lt_error error{};
    lt_guest_result guest_result{};
    uint32_t guest_operation = 0;
    char guest_argument[1024]{};
    uint64_t guest_id = 0;
    uint64_t pcm_socket_dropped = 0;
    uint32_t button = 0, value = 0;
    uint64_t mapping_bytes = 0;
};
struct AudioPacket {
    uint32_t size = sizeof(AudioPacket), version = 1;
    lt_pcm_info info{sizeof(lt_pcm_info), 1, 0, 0, 0, 0, 0, 0};
    std::array<int16_t, 8192> samples{};
};
static bool send_packet(int socket, const Packet &p, int fd = -1) {
    iovec iov{const_cast<Packet *>(&p), sizeof p};
    msghdr message{};
    message.msg_iov = &iov;
    message.msg_iovlen = 1;
    char control[CMSG_SPACE(sizeof(int))]{};
    if (fd >= 0) {
        message.msg_control = control;
        message.msg_controllen = sizeof control;
        auto *c = CMSG_FIRSTHDR(&message);
        c->cmsg_level = SOL_SOCKET;
        c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(c), &fd, sizeof fd);
    }
    ssize_t n = sendmsg(socket, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
    return n == sizeof p;
}
static bool receive(int socket, Packet &p, int *fd = nullptr) {
    iovec iov{&p, sizeof p};
    msghdr message{};
    message.msg_iov = &iov;
    message.msg_iovlen = 1;
    char control[CMSG_SPACE(sizeof(int))]{};
    message.msg_control = control;
    message.msg_controllen = sizeof control;
    ssize_t n = recvmsg(socket, &message, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        return false;
    if (n == 0)
        throw std::runtime_error("worker control channel closed");
    require(n == sizeof p && !(message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) && p.size == sizeof p && p.version == 1,
            "invalid worker protocol");
    for (auto *c = CMSG_FIRSTHDR(&message); c; c = CMSG_NXTHDR(&message, c))
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS) {
            require(c->cmsg_len == CMSG_LEN(sizeof(int)), "invalid frame FD");
            int passed = -1;
            memcpy(&passed, CMSG_DATA(c), sizeof passed);
            if (fd)
                *fd = passed;
            else
                close(passed);
        }
    return true;
}
static void peer(int socket) {
    ucred credentials{};
    socklen_t length = sizeof credentials;
    require(getsockopt(socket, SOL_SOCKET, SO_PEERCRED, &credentials, &length) == 0 && credentials.uid == geteuid(),
            "worker peer UID mismatch");
}
struct Options {
    std::string base, state, rom, engine, package, import, sha, screenshot, script, library, record, name;
    bool recover = false, headless = false, audio = true, worker = false, list = false, library_operation = false;
    bool debug_qmp = false, trace_io = false;
    int control = -1, pcm = -1;
    double seconds = 0;
};
static Options options(int argc, char **argv) {
    Options o;
    std::map<std::string, std::string *> fields = {
        {"--base", &o.base},       {"--state", &o.state},           {"--rom", &o.rom},
        {"--engine", &o.engine},   {"--package", &o.package},       {"--import", &o.import},
        {"--library", &o.library}, {"--record", &o.record},         {"--name", &o.name},
        {"--sha256", &o.sha},      {"--screenshot", &o.screenshot}, {"--script", &o.script}};
    for (int i = 1; i < argc; i++) {
        std::string key = argv[i];
        if (fields.count(key)) {
            require(i + 1 < argc, "missing " + key);
            *fields[key] = argv[++i];
        } else if (key == "--list")
            o.list = true;
        else if (key == "--recover")
            o.recover = true;
        else if (key == "--headless")
            o.headless = true;
        else if (key == "--no-audio")
            o.audio = false;
        else if (key == "--trace-io")
            o.trace_io = true;
        else if (key == "--debug-qmp")
            o.debug_qmp = true;
        else if (key == "--seconds") {
            require(i + 1 < argc, "missing duration");
            o.seconds = std::stod(argv[++i]);
            require(o.seconds >= 0, "invalid duration");
        } else if (key == "--worker") {
            require(i + 2 < argc, "missing worker descriptors");
            o.worker = true;
            o.control = std::stoi(argv[++i]);
            o.pcm = std::stoi(argv[++i]);
        } else if (key == "--help") {
            std::cout
                << "lighttouch-linux --base DIR --state DIR --engine LIB [--rom FILE] [--package ITPACK]\n  [--import "
                   "ITPACK --sha256 HASH] [--recover] [--headless --seconds N]\n  [--script FILE] [--screenshot BMP] "
                   "[--no-audio] [--trace-io]\n\nMouse left/right: contacts 1/2. H: Home, P: Power, Space: pause, "
                   "Escape: orderly "
                   "stop.\nF6: labeled deterministic two-contact pinch. Arrows: acceleration replay (not hardware "
                   "gyro).\nScript: seconds touch ID phase x y; button ID down; pause 0/1; cancel; stop.\n";
            std::exit(0);
        } else
            throw std::runtime_error("unknown option: " + key);
    }
    require(!o.base.empty() || !o.library.empty(), "--base or --library required");
    if (o.state.empty() && !o.base.empty())
        o.state = fs::absolute(o.base).parent_path().string() + "/state";
    for (auto *p : {&o.base, &o.state, &o.rom, &o.engine, &o.package, &o.import, &o.screenshot, &o.script, &o.library})
        if (!p->empty())
            *p = fs::absolute(*p).string();
    return o;
}
static void prepare_library(Options &o) {
    if (o.library.empty())
        return;
    lt_error e{sizeof(e), 1, 0, {}};
    lt_library *l = nullptr;
    lt_library_config c{sizeof(c), 1, o.library.c_str()};
    check(lt_library_open(&c, &l, &e), e);
    struct Handle {
        lt_library *l;
        ~Handle() { lt_library_close(l); }
    } handle{l};
    lt_record_info info{};
    info.size = sizeof(info);
    info.version = 1;
    if (!o.worker && !o.import.empty()) {
        lt_library_import_config cfg{sizeof(cfg),
                                     1,
                                     o.import.c_str(),
                                     o.sha.empty() ? nullptr : o.sha.c_str(),
                                     o.name.empty() ? nullptr : o.name.c_str(),
                                     o.rom.empty() ? nullptr : o.rom.c_str(),
                                     o.package.empty() ? nullptr : o.package.c_str(),
                                     0,
                                     nullptr,
                                     nullptr};
        check(lt_library_import(l, &cfg, &info, &e), e);
        o.record = info.id;
        o.import.clear();
        o.library_operation = true;
        std::cout << "imported record " << info.id << " " << info.board << " " << info.build << "\n";
    }
    if (!o.worker && (o.list || (o.engine.empty() && o.record.empty()))) {
        uint32_t count = 0;
        check(lt_library_count(l, &count, &e), e);
        for (uint32_t i = 0; i < count; i++) {
            check(lt_library_record_at(l, i, &info, &e), e);
            std::cout << info.id << "\t" << info.display_name << "\t" << info.board << "-" << info.build << "\t"
                      << (info.selected ? "selected" : "") << "\t" << (info.dirty ? "dirty" : "clean") << "\n";
        }
        o.library_operation = true;
        if (o.record.empty())
            return;
    }
    if (!o.record.empty())
        check(lt_library_select(l, o.record.c_str(), &e), e);
    check(lt_library_selected(l, &info, &e), e);
    o.record = info.id;
    o.base = info.base_path;
    o.state = info.state_path;
    if (o.rom.empty())
        o.rom = info.bootrom_path;
    if (o.package.empty())
        o.package = info.guest_package_path;
}
static int worker(const Options &o) {
    peer(o.control);
    peer(o.pcm);
    lt_error error{sizeof(error), 1, 0, {}};
    lt_device *d = nullptr;
    lt_device_config dc{sizeof(dc), 1, o.base.c_str(), o.state.c_str(), uint32_t(o.recover)};
    if (o.library.empty())
        check(lt_device_open(&dc, &d, &error), error);
    else {
        lt_library *l = nullptr;
        lt_library_config cfg{sizeof(cfg), 1, o.library.c_str()};
        check(lt_library_open(&cfg, &l, &error), error);
        lt_status status = lt_library_device_open(l, o.record.c_str(), uint32_t(o.recover), &d, &error);
        lt_library_close(l);
        check(status, error);
    }
    lt_runtime *r = nullptr;
    std::string serial = o.state + "/serial.log";
    lt_runtime_config config{sizeof(config),
                             1,
                             o.engine.c_str(),
                             d,
                             {sizeof(lt_boot_config), 1, nullptr, o.rom.empty() ? nullptr : o.rom.c_str(),
                              o.package.empty() ? nullptr : o.package.c_str(), serial.c_str(), uint32_t(o.audio)}};
    check(lt_runtime_create(&config, &r, &error), error);
    if (o.debug_qmp) {
        check(lt_runtime_debug_qmp_enable(r, &error), error);
        char path[1024];
        check(lt_runtime_debug_qmp_path(r, path, sizeof(path), &error), error);
        std::cerr << "debug_qmp worker_pid=" << getpid() << " private_path=" << path << "\n";
    }
    Packet hello;
    hello.kind = Hello;
    int fd = -1;
    check(lt_runtime_frame_fd(r, &fd, &hello.mapping_bytes, &error), error);
    require(send_packet(o.control, hello, fd), "send worker frame FD");
    close(fd);
    check(lt_runtime_start(r, &error), error);
    uint64_t last_status = 0, stopping_since = 0, reported_input = 0, pending_guest = 0, pcm_socket_dropped = 0;
    for (;;) {
        Packet p;
        for (int budget = 0; budget < 32 && receive(o.control, p); budget++) {
            lt_status result = LT_INVALID;
            switch (p.kind) {
            case Touch:
                result = lt_runtime_touch(r, &p.touch, &error);
                if (o.trace_io) {
                    lt_input_status admitted{sizeof(admitted), 1, 0, 0, 0};
                    lt_error trace_error{sizeof(trace_error), 1, 0, {}};
                    auto status = lt_runtime_input_status(r, &admitted, &trace_error);
                    std::ostringstream line;
                    line << "trace_touch observed_monotonic_ns=" << now_ns() << " result=" << result
                         << " request=" << (status == LT_OK ? admitted.request_id : 0) << " contacts=" << p.touch.count;
                    for (uint32_t index = 0; index < std::min(p.touch.count, LT_MAX_CONTACTS); ++index) {
                        const auto &contact = p.touch.contacts[index];
                        line << " contact=" << contact.id << ":" << contact.phase << ":" << contact.x << ":"
                             << contact.y;
                    }
                    std::cout << line.str() << std::endl;
                }
                break;
            case Button:
                result = lt_runtime_button(r, p.button, p.value, &error);
                break;
            case Pause:
                result = lt_runtime_pause(r, p.value, &error);
                break;
            case Stop:
                result = lt_runtime_request_shutdown(r, &error);
                if (result == LT_OK && !stopping_since)
                    stopping_since = now_ns();
                break;
            case Cancel:
                result = lt_runtime_cancel_input(r, &error);
                break;
            case Motion:
                result = lt_runtime_motion(r, &p.motion, &error);
                break;
            case GuestRequest: {
                lt_guest_request request{sizeof(request), 1, p.guest_operation, 65000, p.guest_argument};
                result = lt_runtime_guest_request(r, &request, &pending_guest, &error);
                break;
            }
            case GuestCancel:
                result = lt_runtime_guest_cancel(r, p.guest_id, &error);
                break;
            default:
                break;
            }
            if (result != LT_OK) {
                Packet reply;
                reply.kind = Reply;
                reply.reserved = p.kind;
                reply.error = error;
                send_packet(o.control, reply);
            }
        }
        lt_input_status input{sizeof(input), 1, 0, 0, 0};
        if (lt_runtime_input_status(r, &input, &error) == LT_OK && input.request_id && input.state != 1 &&
            input.request_id != reported_input) {
            reported_input = input.request_id;
            if (o.trace_io)
                std::cout << "trace_applied observed_monotonic_ns=" << now_ns() << " request=" << input.request_id
                          << " state=" << input.state << " engine_result=" << input.engine_result << std::endl;
            Packet report;
            report.kind = InputStatus;
            report.input_status = input;
            if (input.state == 3) {
                lt_runtime_cancel_input(r, &error);
                report.error = {sizeof(lt_error), 1, LT_INVALID, {}};
                snprintf(report.error.message, sizeof(report.error.message),
                         "touch batch %llu rejected by engine (%d); all contacts released",
                         static_cast<unsigned long long>(input.request_id), input.engine_result);
            }
            send_packet(o.control, report);
        }
        if (pending_guest) {
            lt_guest_result guest{sizeof(guest), 1, 0, 0, 0, 0, 0, {}};
            lt_status result = lt_runtime_guest_poll(r, pending_guest, &guest, &error);
            if (result != LT_OK || guest.state != 1) {
                Packet reply;
                reply.kind = GuestResult;
                reply.guest_result = guest;
                if (result != LT_OK)
                    reply.error = error;
                send_packet(o.control, reply);
                pending_guest = 0;
            }
        }
        lt_runtime_status status{sizeof(status), 1, 0, 0, 0, 0, 0, 0};
        lt_status result = lt_runtime_poll(r, &status, &error);
        const lt_error poll_error = error;
        uint64_t now = now_ns();
        if (now - last_status >= 250000000 || status.state == LT_STOPPED || status.state == LT_FAILED) {
            Packet update;
            update.kind = Status;
            update.status = status;
            update.pcm_socket_dropped = pcm_socket_dropped;
            update.diagnostics = {sizeof(lt_runtime_diagnostics), 1, 0, 0, 0, 0, 0, 0, 0, 0};
            lt_runtime_diagnose(r, &update.diagnostics, &error);
            update.io.size = sizeof(update.io);
            update.io.version = 1;
            lt_runtime_io_statistics(r, &update.io, &error);
            if (result != LT_OK)
                update.error = poll_error;
            send_packet(o.control, update);
            last_status = now;
        }
        if (status.state == LT_STOPPED) {
            check(lt_runtime_destroy(r, &error), error);
            lt_device_close(d);
            return status.guest_shutdown_confirmed ? 0 : 2;
        }
        if (status.state == LT_FAILED) {
            std::cerr << "runtime failed: " << poll_error.message << "; dirty state retained\n";
            return 2;
        }
        if (o.audio) {
            for (int budget = 0; budget < 8; budget++) {
                AudioPacket pcm;
                lt_status res = lt_runtime_pcm(r, pcm.samples.data(), 4096, &pcm.info, &error);
                if (res != LT_OK)
                    break;
                if (send(o.pcm, &pcm, sizeof pcm, MSG_DONTWAIT | MSG_NOSIGNAL) != sizeof pcm)
                    pcm_socket_dropped += pcm.info.frames;
            }
        }
        if (stopping_since && lt::elapsed_exceeds(now_ns(), stopping_since, lt::worker_shutdown_timeout_ns)) {
            std::cerr << "guest shutdown timeout; dirty state retained\n";
            return 3;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}
struct Script {
    double at;
    Packet packet;
};
static std::vector<Script> script_file(const std::string &path) {
    std::vector<Script> script;
    if (path.empty())
        return script;
    std::ifstream f(path);
    require(bool(f), "script missing");
    std::string line;
    double previous = -1;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#')
            continue;
        std::istringstream input(line);
        Script s{};
        std::string cmd;
        require(bool(input >> s.at >> cmd) && s.at >= previous, "invalid script order");
        previous = s.at;
        auto &p = s.packet;
        if (cmd == "touch" || cmd == "touches") {
            p.kind = Touch;
            uint32_t count = 1;
            if (cmd == "touches")
                require(bool(input >> count), "script touches missing count");
            p.touch = lt::parse_touch_contacts(input, count);
        } else if (cmd == "button") {
            p.kind = Button;
            require(bool(input >> p.button >> p.value), "invalid button");
        } else if (cmd == "pause") {
            p.kind = Pause;
            require(bool(input >> p.value), "invalid pause");
        } else if (cmd == "screenshot") {
            p.kind = Screenshot;
            std::string path;
            std::getline(input >> std::ws, path);
            require(fs::path(path).is_absolute() && path.size() < sizeof(p.guest_argument),
                    "screenshot needs bounded private absolute path");
            memcpy(p.guest_argument, path.c_str(), path.size() + 1);
        } else if (cmd == "guest") {
            p.kind = GuestRequest;
            std::string operation, argument;
            require(bool(input >> operation), "missing guest operation");
            if (operation == "ping")
                p.guest_operation = LT_GUEST_PING;
            else if (operation == "frontmost")
                p.guest_operation = LT_GUEST_FRONTMOST;
            else if (operation == "launch")
                p.guest_operation = LT_GUEST_LAUNCH_BUILTIN;
            else if (operation == "type")
                p.guest_operation = LT_GUEST_TYPE_TEXT;
            else if (operation == "sync")
                p.guest_operation = LT_GUEST_SYNC;
            else if (operation == "gyro-probe")
                p.guest_operation = LT_GUEST_LAUNCH_GYRO_PROBE;
            else
                throw std::runtime_error("unknown typed guest operation");
            std::getline(input >> std::ws, argument);
            require(argument.size() < sizeof(p.guest_argument), "guest script argument too long");
            memcpy(p.guest_argument, argument.c_str(), argument.size() + 1);
        } else if (cmd == "motion") {
            p.kind = Motion;
            p.motion = {sizeof(lt_motion), 1, 0, 0, 0, {}, {}};
            require(bool(input >> p.motion.kinds >> p.motion.acceleration[0] >> p.motion.acceleration[1] >>
                         p.motion.acceleration[2] >> p.motion.angular_velocity[0] >> p.motion.angular_velocity[1] >>
                         p.motion.angular_velocity[2]),
                    "invalid labeled motion replay");
        } else if (cmd == "cancel")
            p.kind = Cancel;
        else if (cmd == "stop")
            p.kind = Stop;
        else
            throw std::runtime_error("unknown script command");
        script.push_back(s);
    }
    return script;
}
static bool frame_copy(const lt_shared_frame *shared, std::vector<uint8_t> &pixels, lt_frame_info &frame,
                       uint64_t &sequence) {
    for (int attempt = 0; attempt < 3; attempt++) {
        uint64_t before = __atomic_load_n(&shared->generation, __ATOMIC_SEQ_CST);
        if (before & 1)
            continue;
        auto f = shared->frame;
        if (!f.sequence || f.sequence == sequence)
            return false;
        require(f.version == 1 && f.size == sizeof f && f.bytes <= LT_FRAME_CAPACITY && f.stride >= f.width * 4ull &&
                    f.bytes >= uint64_t(f.stride) * f.height && f.width <= 4096 && f.height <= 4096,
                "invalid shared frame bounds");
        pixels.resize(size_t(f.bytes));
        memcpy(pixels.data(), reinterpret_cast<const uint8_t *>(shared) + sizeof(*shared), size_t(f.bytes));
        uint64_t after = __atomic_load_n(&shared->generation, __ATOMIC_SEQ_CST);
        if (before == after && !(after & 1)) {
            frame = f;
            sequence = f.sequence;
            return true;
        }
    }
    return false;
}
static int frontend(const Options &o, int argc, char **argv) {
    if (!o.import.empty()) {
        lt_error e{sizeof(e), 1, 0, {}};
        lt_import_config config{sizeof(config),
                                1,
                                o.import.c_str(),
                                o.base.c_str(),
                                o.sha.c_str(),
                                0,
                                [](void *, uint64_t n, uint64_t total) {
                                    static int last = -1;
                                    int p = int(n * 100 / (total ? total : 1));
                                    if (p != last) {
                                        std::cerr << "import " << p << "%\n";
                                        last = p;
                                    }
                                    return 0;
                                },
                                nullptr};
        check(fs::is_directory(o.import) ? lt_import_directory(&config, &e) : lt_import_archive(&config, &e), e);
    }
    if (o.engine.empty() && !o.import.empty()) {
        std::cout << "prepared base imported: " << o.base << "\n";
        return 0;
    }
    require(!o.engine.empty(), "--engine required for session");
    int sockets[2], audio[2];
    require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets) == 0, "control socketpair");
    require(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, audio) == 0, "audio socketpair");
    pid_t pid = fork();
    require(pid >= 0, "worker fork");
    if (pid == 0) {
        close(sockets[0]);
        close(audio[0]);
        fcntl(sockets[1], F_SETFD, 0);
        fcntl(audio[1], F_SETFD, 0);
        std::string control = std::to_string(sockets[1]), pcm = std::to_string(audio[1]);
        std::vector<char *> args;
        for (int i = 0; i < argc; i++)
            args.push_back(argv[i]);
        if (!o.record.empty()) {
            args.push_back(const_cast<char *>("--record"));
            args.push_back(const_cast<char *>(o.record.c_str()));
        }
        args.push_back(const_cast<char *>("--worker"));
        args.push_back(control.data());
        args.push_back(pcm.data());
        args.push_back(nullptr);
        execv("/proc/self/exe", args.data());
        _exit(127);
    }
    close(sockets[1]);
    close(audio[1]);
    peer(sockets[0]);
    peer(audio[0]);
    struct Child {
        pid_t pid;
        int control, audio;
        ~Child() {
            close(control);
            close(audio);
            int status = 0;
            if (pid > 0 && waitpid(pid, &status, WNOHANG) == 0) {
                kill(pid, SIGTERM);
                bool exited = false;
                for (int i = 0; i < 50; i++) {
                    if (waitpid(pid, &status, WNOHANG) == pid) {
                        exited = true;
                        break;
                    }
                    std::this_thread::sleep_for(std::chrono::milliseconds(10));
                }
                if (!exited) {
                    kill(pid, SIGKILL);
                    waitpid(pid, &status, 0);
                }
            }
        }
    } child{pid, sockets[0], audio[0]};
    Packet hello;
    int fd = -1;
    uint64_t deadline = now_ns() + 60000000000ull;
    while (!receive(sockets[0], hello, &fd)) {
        require(now_ns() < deadline, "worker startup timeout");
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(hello.kind == Hello && fd >= 0 && hello.mapping_bytes == sizeof(lt_shared_frame) + LT_FRAME_CAPACITY,
            "worker handshake invalid");
    auto *shared =
        static_cast<const lt_shared_frame *>(mmap(nullptr, size_t(hello.mapping_bytes), PROT_READ, MAP_SHARED, fd, 0));
    close(fd);
    require(shared != MAP_FAILED, "map shared guest frame");
    require(shared->size == sizeof(*shared) && shared->version == 1 && shared->capacity == LT_FRAME_CAPACITY,
            "frame ABI mismatch");
    struct Mapping {
        const void *p;
        size_t n;
        ~Mapping() { munmap(const_cast<void *>(p), n); }
    } mapping{shared, size_t(hello.mapping_bytes)};
    uint32_t flags = o.headless ? 0 : SDL_INIT_VIDEO | SDL_INIT_EVENTS;
    if (o.audio)
        flags |= SDL_INIT_AUDIO;
    require(SDL_Init(flags) == 0, SDL_GetError());
    struct Sdl {
        ~Sdl() { SDL_Quit(); }
    } sdl;
    SDL_Window *window = nullptr;
    SDL_Renderer *renderer = nullptr;
    SDL_Texture *texture = nullptr;
    SDL_AudioDeviceID sound = 0;
    if (!o.headless) {
        window = SDL_CreateWindow("LightTouch — starting", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 480, 800,
                                  SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
        require(window, SDL_GetError());
        renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED);
        if (!renderer)
            renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
        require(renderer, SDL_GetError());
        SDL_RendererInfo info{};
        require(SDL_GetRendererInfo(renderer, &info) == 0, SDL_GetError());
        std::cout << "SDL renderer=" << info.name << " opaque_format=BGRX32\n";
    }
    if (o.audio) {
        SDL_AudioSpec want{}, got{};
        want.freq = 44100;
        want.format = AUDIO_S16LSB;
        want.channels = 2;
        want.samples = 1024;
        sound = SDL_OpenAudioDevice(nullptr, 0, &want, &got, 0);
        if (sound)
            SDL_PauseAudioDevice(sound, 0);
        else
            std::cerr << "audio device unavailable: " << SDL_GetError() << "\n";
    }
    std::vector<uint8_t> pixels;
    lt_frame_info frame{sizeof(frame), 1, 0, 0, 0, 0, 0, 0, 0};
    uint64_t sequence = 0, start = now_ns();
    bool stopping = false, paused = false, terminal = false;
    uint64_t stop_since = 0;
    uint64_t pcm_received = 0, pcm_nonzero_samples = 0, pcm_loud_samples = 0, pcm_socket_dropped = 0,
             pcm_sdl_queued = 0, pcm_sdl_discarded = 0, pcm_sdl_errors = 0;
    uint32_t pcm_peak_abs = 0;
    std::map<uint32_t, std::pair<double, double>> contacts;
    std::map<SDL_FingerID, uint32_t> fingers;
    uint32_t next_finger = 10000, display_rotation = 0;
    auto script = script_file(o.script);
    size_t next_script = 0;
    lt::ControlDelivery delivery;
    auto submit = [&](Packet p) {
        if (p.kind == Touch)
            p.touch.timestamp_ns = now_ns();
        if (p.kind == Motion)
            p.motion.timestamp_ns = now_ns();
        auto kind = p.kind == Touch || p.kind == Button || p.kind == Motion ? lt::ControlDelivery::Kind::input
                    : p.kind == Cancel                                      ? lt::ControlDelivery::Kind::cancel
                    : p.kind == Pause                                       ? lt::ControlDelivery::Kind::pause
                                                                            : lt::ControlDelivery::Kind::lifecycle;
        bool sent = delivery.send(kind, [&] { return send_packet(sockets[0], p); });
        if (delivery.needs_cancel() || p.kind == Cancel || p.kind == Pause) {
            contacts.clear();
            fingers.clear();
        }
        if (!sent)
            std::cerr << "control backpressure: command " << p.kind << " rejected\n";
        return sent;
    };
    bool screenshot_written = false;
    lt_runtime_diagnostics diagnostics{sizeof(lt_runtime_diagnostics), 1, 0, 0, -1, 0, 0, 0, 0, 0};
    auto capture = [&](const std::string &path) {
        require(frame.sequence && pixels.size(), "no actual guest frame for screenshot");
        lt_error error{sizeof(error), 1, 0, {}};
        check(lt_frame_write_bmp(&frame, pixels.data(), pixels.size(), path.c_str(), &error), error);
        if (path == o.screenshot)
            screenshot_written = true;
        std::cout << "screenshot sequence=" << frame.sequence << " sleeping=" << diagnostics.display_sleeping
                  << " backlight=" << diagnostics.backlight_level << " agent=" << diagnostics.agent_status
                  << " path=" << path << "\n";
    };
    struct CaptureOnExit {
        std::function<void()> function;
        ~CaptureOnExit() { function(); }
    } capture_on_exit{[&] {
        if (!o.screenshot.empty() && !screenshot_written && frame.sequence) {
            try {
                capture(o.screenshot);
            } catch (const std::exception &e) {
                std::cerr << "failure screenshot unavailable: " << e.what() << "\n";
            }
        }
    }};
    auto stop = [&] {
        if (!stopping) {
            if (!o.screenshot.empty() && frame.sequence) {
                try {
                    capture(o.screenshot);
                } catch (const std::exception &e) {
                    std::cerr << "screenshot checkpoint failed: " << e.what() << "\n";
                }
            }
            Packet p;
            p.kind = Stop;
            require(submit(p), "shutdown command was not delivered; worker state remains dirty");
            stopping = true;
            stop_since = now_ns();
        }
    };
    auto touch = [&](uint32_t id, uint32_t phase, double x, double y) {
        Packet p;
        p.kind = Touch;
        p.touch = {sizeof(p.touch), 1, now_ns(), 1, 0, {}};
        p.touch.contacts[0] = {sizeof(lt_contact), 1, id, phase, x, y};
        return submit(p);
    };
    while (!terminal) {
        delivery.retry_cancel([&] {
            Packet cancel;
            cancel.kind = Cancel;
            return send_packet(sockets[0], cancel);
        });
        uint64_t now = now_ns();
        double elapsed = double(now - start) / 1e9;
        while (next_script < script.size() && elapsed >= script[next_script].at) {
            if (script[next_script].packet.kind == Stop)
                stop();
            else if (script[next_script].packet.kind == Screenshot) {
                try {
                    capture(script[next_script].packet.guest_argument);
                } catch (const std::exception &e) {
                    std::cerr << "checkpoint screenshot failed: " << e.what() << "\n";
                }
            } else
                submit(script[next_script].packet);
            next_script++;
        }
        if (o.seconds > 0 && elapsed >= o.seconds)
            stop();
        Packet update;
        while (receive(sockets[0], update)) {
            if (update.kind == Status) {
                const auto &s = update.status;
                if (s.state == LT_PAUSED || s.state == LT_RUNNING)
                    paused = s.state == LT_PAUSED;
                diagnostics = update.diagnostics;
                pcm_socket_dropped = update.pcm_socket_dropped;
                std::cout << "status observed_monotonic_ns=" << now_ns() << " state=" << s.state
                          << " confirmed=" << s.guest_shutdown_confirmed << " dirty=" << s.dirty
                          << " frames=" << s.frames << " sleeping=" << diagnostics.display_sleeping
                          << " backlight=" << diagnostics.backlight_level << " agent=" << diagnostics.agent_status
                          << " shutdown_phase=" << diagnostics.shutdown_phase
                          << " shutdown_route=" << diagnostics.shutdown_route
                          << " shutdown_reply=" << diagnostics.shutdown_reply_received << ":"
                          << diagnostics.shutdown_reply_status << " frame_sequence=" << update.io.frame_sequence
                          << " frame_monotonic_ns=" << update.io.frame_timestamp_ns
                          << " pcm_captured=" << update.io.pcm_captured_frames
                          << " pcm_pulled=" << update.io.pcm_pulled_frames
                          << " pcm_native_dropped=" << update.io.pcm_dropped_frames
                          << " pcm_native_packets=" << update.io.pcm_queued_packets << " pcm_received=" << pcm_received
                          << " pcm_nonzero_samples=" << pcm_nonzero_samples << " pcm_loud_samples=" << pcm_loud_samples
                          << " pcm_peak_abs=" << pcm_peak_abs << " pcm_socket_dropped=" << pcm_socket_dropped
                          << " pcm_sdl_queued=" << pcm_sdl_queued << " pcm_sdl_discarded=" << pcm_sdl_discarded
                          << " pcm_sdl_errors=" << pcm_sdl_errors << "\n";
                if (window) {
                    std::string title = "LightTouch — " + std::string(s.state == LT_RUNNING    ? "running"
                                                                      : s.state == LT_PAUSED   ? "paused"
                                                                      : s.state == LT_STOPPING ? "stopping"
                                                                      : s.state == LT_STARTING ? "starting"
                                                                                               : "stopped");
                    SDL_SetWindowTitle(window, title.c_str());
                }
                if (s.state == LT_STOPPED || s.state == LT_FAILED) {
                    terminal = true;
                    break;
                }
            } else if (update.kind == Reply) {
                std::cerr << "command failed: " << update.error.message << "\n";
                contacts.clear();
                fingers.clear();
                Packet cancel;
                cancel.kind = Cancel;
                // Cancellation can be rejected before the engine becomes ready.
                // Do not create an endless Reply/Cancel loop during startup.
                if (update.reserved != Cancel)
                    submit(cancel);
            } else if (update.kind == GuestResult) {
                const auto &result = update.guest_result;
                std::cout << "guest request=" << result.request_id << " state=" << result.state
                          << " status=" << result.guest_status << " bytes=" << result.bytes << " output="
                          << std::string(result.output, std::min<size_t>(result.bytes, sizeof(result.output))) << "\n";
                if (update.error.code)
                    std::cerr << "guest operation error: " << update.error.message << "\n";
            } else if (update.kind == InputStatus) {
                std::cout << "input batch=" << update.input_status.request_id << " state=" << update.input_status.state
                          << " engine_result=" << update.input_status.engine_result << "\n";
                if (update.input_status.state == 3) {
                    contacts.clear();
                    fingers.clear();
                    std::cerr << update.error.message << "\n";
                }
            }
        }
        AudioPacket pcm;
        for (int budget = 0; budget < 16; budget++) {
            ssize_t n = recv(audio[0], &pcm, sizeof pcm, MSG_DONTWAIT);
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
                break;
            if (n == 0)
                break;
            require(n == sizeof pcm && pcm.version == 1 && pcm.info.frames <= 4096 && pcm.info.channels == 2,
                    "invalid PCM packet");
            pcm_received += pcm.info.frames;
            uint32_t packet_nonzero = 0, packet_first = 0, packet_last = 0;
            for (uint32_t i = 0; i < pcm.info.frames * 2; i++) {
                pcm_nonzero_samples += pcm.samples[i] != 0;
                if (pcm.samples[i]) {
                    if (!packet_nonzero)
                        packet_first = i;
                    packet_last = i;
                    packet_nonzero++;
                }
                uint32_t magnitude = uint32_t(std::abs(int(pcm.samples[i])));
                pcm_loud_samples += magnitude > 1000;
                pcm_peak_abs = std::max(pcm_peak_abs, magnitude);
            }
            if (o.trace_io && packet_nonzero)
                std::cout << "trace_pcm observed_monotonic_ns=" << now_ns() << " frames=" << pcm.info.frames
                          << " capture_relative_ns=" << pcm.info.timestamp_ns << " nonzero_samples=" << packet_nonzero
                          << " first_nonzero_sample=" << packet_first << " last_nonzero_sample=" << packet_last
                          << " sample_rate=" << pcm.info.sample_rate << " channels=" << pcm.info.channels << std::endl;
            if (sound) {
                if (SDL_GetQueuedAudioSize(sound) > 17640) {
                    pcm_sdl_discarded += SDL_GetQueuedAudioSize(sound) / 4;
                    SDL_ClearQueuedAudio(sound);
                }
                if (SDL_QueueAudio(sound, pcm.samples.data(), pcm.info.frames * 4) == 0)
                    pcm_sdl_queued += pcm.info.frames;
                else
                    pcm_sdl_errors++;
            }
        }
        if (frame_copy(shared, pixels, frame, sequence) && renderer) {
            if (o.trace_io)
                std::cout << "trace_frame observed_monotonic_ns=" << now_ns() << " sequence=" << frame.sequence
                          << " source_monotonic_ns=" << frame.timestamp_ns << std::endl;
            if (!texture) {
                texture = lt::opaque_texture(renderer, int(frame.width), int(frame.height));
                require(texture, SDL_GetError());
            } else {
                int width = 0, height = 0;
                require(SDL_QueryTexture(texture, nullptr, nullptr, &width, &height) == 0,
                        std::string("SDL texture query: ") + SDL_GetError());
                if (width != int(frame.width) || height != int(frame.height)) {
                    SDL_DestroyTexture(texture);
                    texture = lt::opaque_texture(renderer, int(frame.width), int(frame.height));
                    require(texture, SDL_GetError());
                }
            }
            require(SDL_UpdateTexture(texture, nullptr, pixels.data(), int(frame.stride)) == 0,
                    std::string("SDL texture update: ") + SDL_GetError());
        }
        if (renderer && texture) {
            int width = 0, height = 0;
            require(SDL_GetRendererOutputSize(renderer, &width, &height) == 0,
                    std::string("SDL renderer output: ") + SDL_GetError());
            double display_width = (display_rotation % 180) ? frame.height : frame.width;
            double display_height = (display_rotation % 180) ? frame.width : frame.height;
            double scale = std::min(double(width) / display_width, double(height) / display_height);
            SDL_Rect dest{int((width - frame.width * scale) / 2), int((height - frame.height * scale) / 2),
                          int(frame.width * scale), int(frame.height * scale)};
            require(SDL_SetRenderDrawColor(renderer, 24, 24, 24, 255) == 0,
                    std::string("SDL clear color: ") + SDL_GetError());
            require(SDL_RenderClear(renderer) == 0, std::string("SDL clear: ") + SDL_GetError());
            require(SDL_RenderCopyEx(renderer, texture, nullptr, &dest, display_rotation, nullptr, SDL_FLIP_NONE) == 0,
                    std::string("SDL rotated frame: ") + SDL_GetError());
            SDL_RenderPresent(renderer);
        }
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT)
                stop();
            else if (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_FOCUS_LOST) {
                Packet p;
                p.kind = Cancel;
                submit(p);
                contacts.clear();
                fingers.clear();
            } else if (event.type == SDL_KEYDOWN || event.type == SDL_KEYUP) {
                bool down = event.type == SDL_KEYDOWN;
                if (event.key.repeat)
                    continue;
                auto key = event.key.keysym.sym;
                if (key == SDLK_ESCAPE && down)
                    stop();
                else if (key == SDLK_SPACE && down) {
                    paused = !paused;
                    Packet p;
                    p.kind = Pause;
                    p.value = paused;
                    submit(p);
                } else if (key == SDLK_s && down) {
                    std::string path = o.screenshot.empty()
                                           ? o.state + "/screenshot-" + std::to_string(frame.sequence) + ".bmp"
                                           : o.screenshot;
                    try {
                        capture(path);
                    } catch (const std::exception &e) {
                        std::cerr << "live screenshot failed: " << e.what() << "\n";
                    }
                } else if (key == SDLK_r && down) {
                    Packet p;
                    p.kind = Cancel;
                    submit(p);
                    contacts.clear();
                    fingers.clear();
                    display_rotation = (display_rotation + 90) % 360;
                } else if (key == SDLK_h || key == SDLK_p || key == SDLK_EQUALS || key == SDLK_MINUS) {
                    Packet p;
                    p.kind = Button;
                    p.button = key == SDLK_h ? 0 : key == SDLK_p ? 1 : key == SDLK_EQUALS ? 2 : 3;
                    p.value = down;
                    submit(p);
                } else if (key == SDLK_F6 && down) {
                    std::cerr << "deterministic touch replay (guest acceptance requires observation)\n";
                    for (int step = 0; step <= 12; step++) {
                        Packet p;
                        p.kind = Touch;
                        p.touch = {sizeof(p.touch), 1, 0, 2, 0, {}};
                        uint32_t phase = step == 0 ? 0 : step == 12 ? 2 : 1;
                        double delta = .1 + step * .015;
                        p.touch.contacts[0] = {sizeof(lt_contact), 1, 101, phase, .5 - delta, .5};
                        p.touch.contacts[1] = {sizeof(lt_contact), 1, 102, phase, .5 + delta, .5};
                        script.push_back({elapsed + step * .08, p});
                    }
                    std::sort(script.begin() + next_script, script.end(),
                              [](const Script &a, const Script &b) { return a.at < b.at; });
                } else if (down && (key == SDLK_LEFT || key == SDLK_RIGHT || key == SDLK_UP || key == SDLK_DOWN)) {
                    Packet p;
                    p.kind = Motion;
                    p.motion = {sizeof(lt_motion), 1, now_ns(), 1, 0, {0, 9.80665, 0}, {0, 0, 0}};
                    if (key == SDLK_LEFT || key == SDLK_RIGHT)
                        p.motion.acceleration[0] = key == SDLK_LEFT ? -9.80665 : 9.80665;
                    else
                        p.motion.acceleration[1] = key == SDLK_UP ? 9.80665 : -9.80665;
                    submit(p);
                    std::cerr << "manual acceleration replay; not physical gyro\n";
                }
            } else if (frame.width &&
                       (event.type == SDL_FINGERDOWN || event.type == SDL_FINGERMOTION || event.type == SDL_FINGERUP)) {
                int width = 0, height = 0;
                SDL_GetWindowSize(window, &width, &height);
                double display_width = (display_rotation % 180) ? frame.height : frame.width,
                       display_height = (display_rotation % 180) ? frame.width : frame.height;
                double scale = std::min(double(width) / display_width, double(height) / display_height),
                       left = (width - display_width * scale) / 2, top = (height - display_height * scale) / 2, x = 0,
                       y = 0;
                lt_status mapped =
                    lt_map_contact(event.tfinger.x * width, event.tfinger.y * height, left, top, display_width * scale,
                                   display_height * scale, display_rotation, &x, &y);
                if (event.type == SDL_FINGERDOWN && mapped == LT_OK) {
                    uint32_t id = next_finger++;
                    fingers[event.tfinger.fingerId] = id;
                    contacts[id] = {x, y};
                    touch(id, 0, x, y);
                } else if (fingers.count(event.tfinger.fingerId)) {
                    uint32_t id = fingers[event.tfinger.fingerId];
                    if (event.type == SDL_FINGERUP) {
                        auto c = contacts[id];
                        touch(id, 2, c.first, c.second);
                        contacts.erase(id);
                        fingers.erase(event.tfinger.fingerId);
                    } else if (mapped == LT_OK) {
                        contacts[id] = {x, y};
                        touch(id, 1, x, y);
                    }
                }
            } else if (frame.width && (event.type == SDL_MOUSEBUTTONDOWN || event.type == SDL_MOUSEBUTTONUP ||
                                       event.type == SDL_MOUSEMOTION)) {
                if ((event.type == SDL_MOUSEMOTION && event.motion.which == SDL_TOUCH_MOUSEID) ||
                    (event.type != SDL_MOUSEMOTION && event.button.which == SDL_TOUCH_MOUSEID))
                    continue;
                int width = 0, height = 0;
                SDL_GetWindowSize(window, &width, &height);
                double display_width = (display_rotation % 180) ? frame.height : frame.width,
                       display_height = (display_rotation % 180) ? frame.width : frame.height;
                double scale = std::min(double(width) / display_width, double(height) / display_height),
                       left = (width - display_width * scale) / 2, top = (height - display_height * scale) / 2;
                double x = 0, y = 0;
                int px = event.type == SDL_MOUSEMOTION ? event.motion.x : event.button.x,
                    py = event.type == SDL_MOUSEMOTION ? event.motion.y : event.button.y;
                lt_status mapped = lt_map_contact(px, py, left, top, display_width * scale, display_height * scale,
                                                  display_rotation, &x, &y);
                if (event.type == SDL_MOUSEMOTION) {
                    // A failed send clears local ownership. Iterate a snapshot
                    // so cancellation cannot invalidate this traversal.
                    const auto held = contacts;
                    for (const auto &c : held) {
                        if (mapped == LT_OK) {
                            contacts[c.first] = {x, y};
                            if (!touch(c.first, 1, x, y))
                                break;
                        }
                    }
                } else {
                    uint32_t id = event.button.button == SDL_BUTTON_LEFT ? 1 : 2;
                    if (event.type == SDL_MOUSEBUTTONDOWN && mapped == LT_OK) {
                        contacts[id] = {x, y};
                        touch(id, 0, x, y);
                    } else if (event.type == SDL_MOUSEBUTTONUP && contacts.count(id)) {
                        auto c = contacts[id];
                        touch(id, 2, c.first, c.second);
                        contacts.erase(id);
                    }
                }
            }
        }
        if (stopping && lt::elapsed_exceeds(now_ns(), stop_since, lt::parent_shutdown_timeout_ns)) {
            throw std::runtime_error("shutdown timeout; worker terminated, dirty state preserved");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (!o.screenshot.empty() && !screenshot_written)
        capture(o.screenshot);
    if (sound)
        SDL_CloseAudioDevice(sound);
    if (texture)
        SDL_DestroyTexture(texture);
    if (renderer)
        SDL_DestroyRenderer(renderer);
    if (window)
        SDL_DestroyWindow(window);
    int status = 0;
    waitpid(pid, &status, 0);
    child.pid = -1;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 2;
}
int main(int argc, char **argv) {
    try {
        auto o = options(argc, argv);
        prepare_library(o);
        if (!o.worker && !o.library.empty() && o.engine.empty())
            return 0;
        return o.worker ? worker(o) : frontend(o, argc, argv);
    } catch (const std::exception &e) {
        std::cerr << "LightTouch: " << e.what() << "\n";
        return 1;
    }
}
