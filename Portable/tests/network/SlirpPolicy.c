/* Exercise the actual vendored NAT translation, including the explicit DNS
 * exemption. This fixture performs no outbound connections. */
#include "slirp.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static bool route(Slirp *slirp, const char *target, bool dns) {
    struct socket so = {0};
    struct sockaddr_storage address = {0};
    so.slirp = slirp;
    if (strchr(target, ':')) {
        address.ss_family = AF_INET6;
        assert(inet_pton(AF_INET6, target, &((struct sockaddr_in6 *)&address)->sin6_addr) == 1);
        so.so_ffamily = AF_INET6;
        so.so_faddr6 = ((struct sockaddr_in6 *)&address)->sin6_addr;
    } else {
        address.ss_family = AF_INET;
        assert(inet_pton(AF_INET, target, &((struct sockaddr_in *)&address)->sin_addr) == 1);
        so.so_ffamily = AF_INET;
        so.so_faddr = ((struct sockaddr_in *)&address)->sin_addr;
    }
    so.so_type = dns ? IPPROTO_UDP : IPPROTO_TCP;
    so.so_fport = htons(dns ? 53 : 80);
    return sotranslate_out(&so, &address) == 0;
}
int main(void) {
    Slirp slirp = {0};
    assert(inet_pton(AF_INET, "10.0.2.2", &slirp.vhost_addr) == 1);
    assert(inet_pton(AF_INET, "10.0.2.3", &slirp.vnameserver_addr) == 1);
    assert(inet_pton(AF_INET6, "fec0::", &slirp.vprefix_addr6) == 1);
    slirp.vprefix_len = 64;
    struct sockaddr_in resolver = {0};
    resolver.sin_family = AF_INET;
    resolver.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    resolver.sin_port = htons(5353);
    slirp_set_dns_forward(&slirp, &resolver);
    for (int local = 0; local <= 1; local++) {
        slirp_set_local_network(&slirp, local);
        assert(route(&slirp, "93.184.216.34", false));
        assert(route(&slirp, "2606:2800:220:1:248:1893:25c8:1946", false));
        assert(route(&slirp, "10.0.2.3", true));
        assert(!route(&slirp, "10.0.2.3", false));
        assert(!route(&slirp, "10.0.2.2", false));
        assert(!route(&slirp, "127.0.0.1", false));
        assert(!route(&slirp, "0.0.0.1", false));
        assert(!route(&slirp, "::1", false));
        assert(!route(&slirp, "::ffff:127.0.0.1", false));
        assert(!route(&slirp, "64:ff9b::7f00:1", false));
        assert(!route(&slirp, "::ffff:0:127.0.0.1", false));
        for (size_t i = 0; i < 3; i++) {
            const char *public4[] = {"::ffff:93.184.216.34", "64:ff9b::5db8:d822", "::ffff:0:93.184.216.34"};
            assert(route(&slirp, public4[i], false));
        }
        const char *private[] = {
            "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.1.1",
            "100.64.0.1", "198.18.0.1", "240.0.0.1", "fd00::1", "fed0::1",
            "::ffff:192.168.1.1", "64:ff9b::c0a8:101", "::ffff:0:192.168.1.1"
        };
        for (size_t i = 0; i < sizeof(private) / sizeof(private[0]); i++) {
            assert(route(&slirp, private[i], false) == !!local);
        }
    }
    puts("PASS: actual slirp NAT LAN policy, host-loopback isolation and Android DNS forwarding");
}
