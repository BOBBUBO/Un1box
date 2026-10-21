// lighttouch-oneshot: the engine run headless on one QEMU command line, for FirmwareKit's preparation boots
// (keybag, seal) where LightTouchDevice, the Mac's helper, is not available (Linux, Android).
//
//   lighttouch-oneshot LightTouch -M <machine,...> -serial file:<log> ...   (argv as BootRecipe makes it)
//   lighttouch-oneshot '{"mode":{"machines":{}}}'                         (LightTouchDevice's machine listing)
//
// The engine is $LTM_QEMU_DYLIB, else libqemu-arm.so beside this program. SIGTERM or SIGINT quits QEMU as the
// helper does (qemu_ios_ui_quit: storage flushed); the exit status is qemu_ios_main's. FirmwareKit watches the
// serial log and the clock itself (Preparer.oneshot).
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <limits.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdbool.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <android/api-level.h>
#endif

/* qemu-ios contrib/ios-app/qemu-ios-ui.h */
typedef struct {
    const char *machine, *board;
    int screen_width, screen_height, screen_scale, default_orientation;
    bool has_cellular, has_usb_host, has_compass, has_usb_charger;
    int panel_min, panel_max_width, panel_max_height, panel_width_step, panel_max_pixels;
} QemuIosDeviceInfo;
typedef const QemuIosDeviceInfo *(*info_at_fn)(int);

typedef int (*main_fn)(int, char **);
typedef void (*attach_fn)(const void *, const void *);
typedef void (*void_fn)(void);

static void_fn quit;
static int wake[2];

// The engine's threads on the fast cores only, as Portable's runtime does for a running device (Runtime.cpp
// prefer_fast_cores): Android's scheduler otherwise leaves the second TCG thread and the main loop on the little
// cores, where they hold the big lock for longer. No-op unless the slowest core has under half the biggest's
// capacity (desktops); LT_ENGINE_CPUS=off skips it.
static void prefer_fast_cores(void) {
#ifdef __linux__
    const char *env = getenv("LT_ENGINE_CPUS");
    if (env && !strcmp(env, "off")) return;
    long capacity[CPU_SETSIZE], biggest = 0, smallest = LONG_MAX;
    int n = 0;
    for (; n < CPU_SETSIZE; n++) {
        char path[96];
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d", n);
        if (access(path, F_OK) != 0) break;
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/cpu_capacity", n);
        FILE *f = fopen(path, "re");
        capacity[n] = 0;  // unknown (e.g. offline): never selected
        if (f) {
            if (fscanf(f, "%ld", &capacity[n]) != 1) capacity[n] = 0;
            fclose(f);
        }
        if (capacity[n] > biggest) biggest = capacity[n];
        if (capacity[n] > 0 && capacity[n] < smallest) smallest = capacity[n];
    }
    if (n == 0 || biggest <= 0 || smallest * 2 > biggest) return;
    cpu_set_t fast;
    CPU_ZERO(&fast);
    for (int cpu = 0; cpu < n; cpu++)
        if (capacity[cpu] * 2 >= biggest) CPU_SET(cpu, &fast);
    if (CPU_COUNT(&fast) > 0) sched_setaffinity(0, sizeof fast, &fast);
#endif
}

#ifdef __ANDROID__
// Android Dynamic Performance Framework for the vCPU threads, as the app's runtime does for a running device
// (performance_hint.h), but for a boot that has no frames and wants to finish soonest: every 20 ms, the vCPUs'
// CPU time in that window against a 10 ms target, so a busy vCPU reads as behind and the power HAL raises its clocks
// and placement (2.3x on a Pixel's guest), and an idle one is left alone. API 33+, resolved at run time; does nothing
// without hint sessions (the Poco's HAL). LT_ADPF=off skips it.
typedef struct APerformanceHintManager APerformanceHintManager;
typedef struct APerformanceHintSession APerformanceHintSession;

static int vcpu_tids(int32_t *tids, int max) {
    DIR *tasks = opendir("/proc/self/task");
    int n = 0;
    if (!tasks) return 0;
    for (struct dirent *task; n < max && (task = readdir(tasks));) {
        char path[64], name[32] = {0};
        snprintf(path, sizeof path, "/proc/self/task/%s/comm", task->d_name);
        FILE *f = fopen(path, "re");
        if (!f) continue;
        if (!fgets(name, sizeof name, f)) name[0] = 0;
        fclose(f);
        size_t len = strcspn(name, "\n");
        name[len] = 0;
        if (!strncmp(name, "CPU ", 4) && len > 4 && !strcmp(name + len - 4, "/TCG")) tids[n++] = atoi(task->d_name);
    }
    closedir(tasks);
    return n;
}

static int64_t cpu_ns(const int32_t *tids, int n) {
    int64_t total = 0;
    for (int i = 0; i < n; i++) {
        struct timespec value;  // another thread's CPU clock: MAKE_THREAD_CPUCLOCK(tid, CPUCLOCK_SCHED)
        if (clock_gettime((clockid_t)((~(unsigned)tids[i] << 3) | 6), &value) != 0) return -1;
        total += (int64_t)value.tv_sec * 1000000000 + value.tv_nsec;
    }
    return total;
}

static void *performance_hint(void *arg) {
    (void)arg;
    void *android = dlopen("libandroid.so", RTLD_NOW | RTLD_LOCAL);
    APerformanceHintManager *(*manager)(void) = android ? dlsym(android, "APerformanceHint_getManager") : NULL;
    APerformanceHintSession *(*create)(APerformanceHintManager *, const int32_t *, size_t, int64_t) =
        android ? dlsym(android, "APerformanceHint_createSession") : NULL;
    int (*report)(APerformanceHintSession *, int64_t) =
        android ? dlsym(android, "APerformanceHint_reportActualWorkDuration") : NULL;
    if (!manager || !create || !report || !manager()) return NULL;
    int32_t tids[8];
    int n = 0;
    for (int tries = 0; tries < 60 && (n = vcpu_tids(tids, 8)) == 0; tries++) sleep(1);  // QEMU names them at start
    APerformanceHintSession *session = n ? create(manager(), tids, (size_t)n, 10000000) : NULL;
    if (!session) return NULL;
    for (int64_t last = cpu_ns(tids, n), now; last >= 0; last = now) {
        struct timespec period = {0, 20000000};
        nanosleep(&period, NULL);
        if ((now = cpu_ns(tids, n)) < 0) break;  // a vCPU thread is gone: QEMU is stopping
        if (now > last) report(session, now - last);
    }
    return NULL;
}
#endif

static void on_signal(int sig) {
    (void)sig;
    char c = 1;
    (void)!write(wake[1], &c, 1);
}

static void *quitter(void *arg) {
    (void)arg;
    char c;
    while (read(wake[0], &c, 1) != 1) {}
    quit();
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s argv0 qemu-arguments...\n", argv[0]);
        return 64;
    }
    char engine[PATH_MAX + 32];
    const char *env = getenv("LTM_QEMU_DYLIB");
    if (env) {
        snprintf(engine, sizeof engine, "%s", env);
    } else {
        char self[PATH_MAX];
        ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
        if (n <= 0) return 70;
        self[n] = 0;
        char *slash = strrchr(self, '/');
        if (slash) *slash = 0;
        snprintf(engine, sizeof engine, "%s/libqemu-arm.so", self);
    }
    void *handle = dlopen(engine, RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        fprintf(stderr, "lighttouch-oneshot: %s\n", dlerror());
        return 70;
    }
    if (argc == 2 && argv[1][0] == '{' && strstr(argv[1], "\"machines\"")) {
        // HostRuntime DeviceInfo.list: {"dylibPath", "machines": [DeviceInfo]}
        info_at_fn info_at = (info_at_fn)dlsym(handle, "qemu_ios_device_info_at");
        if (!info_at) return 70;
        printf("{\"dylibPath\":\"%s\",\"machines\":[", engine);
        for (int i = 0; info_at(i); i++) {
            const QemuIosDeviceInfo *d = info_at(i);
            printf("%s{\"machine\":\"%s\",\"board\":\"%s\",\"screenWidth\":%d,\"screenHeight\":%d,"
                   "\"screenScale\":%d,\"defaultOrientation\":%d,\"hasCellular\":%s,\"hasUSBHost\":%s,"
                   "\"hasCompass\":%s,\"panelMin\":%d,\"panelMaxWidth\":%d,\"panelMaxHeight\":%d,"
                   "\"panelWidthStep\":%d,\"panelMaxPixels\":%d}",
                   i ? "," : "", d->machine, d->board, d->screen_width, d->screen_height, d->screen_scale,
                   d->default_orientation, d->has_cellular ? "true" : "false", d->has_usb_host ? "true" : "false",
                   d->has_compass ? "true" : "false", d->panel_min, d->panel_max_width, d->panel_max_height,
                   d->panel_width_step, d->panel_max_pixels);
        }
        printf("]}\n");
        return 0;
    }
    main_fn qemu_main = (main_fn)dlsym(handle, "qemu_ios_main");
    attach_fn attach = (attach_fn)dlsym(handle, "qemu_ios_ui_attach");
    quit = (void_fn)dlsym(handle, "qemu_ios_ui_quit");
    if (!qemu_main || !attach || !quit) {
        fprintf(stderr, "lighttouch-oneshot: %s lacks the qemu_ios entry points\n", engine);
        return 70;
    }
    prefer_fast_cores();  // before any thread starts, so they all inherit it
    if (pipe(wake) != 0) return 71;
    pthread_t t;
    pthread_create(&t, NULL, quitter, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    attach(NULL, NULL);
#ifdef __ANDROID__
    const char *adpf = getenv("LT_ADPF");
    pthread_t hint;
    if (android_get_device_api_level() >= 33 && !(adpf && !strcmp(adpf, "off")) &&
        pthread_create(&hint, NULL, performance_hint, NULL) == 0)
        pthread_detach(hint);
#endif
    return qemu_main(argc - 1, argv + 1);
}
