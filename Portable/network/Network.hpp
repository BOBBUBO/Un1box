#pragma once
#include <cstdint>
#include <memory>
#include <string>

// Shared host web backend. No Android/JNI/UI types. OpenSSL bridges legacy guest
// TLS to verified modern origin TLS; only the local listener is platform specific.
namespace lt::network {
enum class Mode { off, direct, archive };
struct Configuration {
    Mode mode = Mode::off;
    std::string archive_date = "20090909";
    bool local_network = false;
    static bool valid_date(const std::string &date);
};
struct Address {
    std::string scheme, host, path;
    unsigned port = 0;
    static Address parse(const std::string &url);
    std::string url() const;
};
// Same address policy for raw NAT and HTTP: host loopback never, private ranges
// only by explicit opt-in. Mapped/translated/NAT64 IPv4 is classified as IPv4.
bool public_address(const unsigned char *bytes, size_t count);
std::string archive_url(const std::string &target, const std::string &date);
std::string archive_redirect(const std::string &current, const std::string &location,
                             const std::string &date);

class Proxy {
public:
    // directory is per-device private storage. CA keys never leave it. ca_directory
    // optionally supplies the host's roots (Android Conscrypt's certificate directory).
    Proxy(std::string directory, Configuration configuration, std::string ca_directory = "");
    ~Proxy();
    Proxy(const Proxy &) = delete;
    Proxy &operator=(const Proxy &) = delete;
    std::string certificate() const; // public DER for the guest trust store
    std::string fingerprint() const;
    // Unix listener for libslirp's per-connection guestfwd. Windows frontends may
    // call serve() on an accepted socket instead, sharing routing and TLS unchanged.
    void listen(const std::string &socket_path);
    void serve(std::intptr_t connected_socket); // borrows the socket
    std::string status() const;
private:
    struct State;
    std::unique_ptr<State> state;
};
} // namespace lt::network
