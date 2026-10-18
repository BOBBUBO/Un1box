/* LT Bench: microbenchmarks for the guest paths an emulator makes expensive.
 *
 * A game's frame rate mixes everything; this splits it into the paths QEMU
 * handles outside straight-line translated code: system calls and exceptions,
 * context switches between threads and between address spaces, page faults,
 * TLB reach, indirect branches and returns, and plain compute and memory as the
 * baseline. Each prints ns per operation (best and median of several rounds) to
 * the engine log through the QEMU_CALL log channel and on screen.
 * See docs/GUEST-BENCHMARKS.md.
 *
 * iPhone OS 3.1.3, ARMv6 ARM state, no SDK: public libSystem/UIKit calls are
 * declared here with their ARM32 ABI (the pattern of
 * Linux/tests/guest-install-probe/probe.c).
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
typedef unsigned long size_t;
typedef unsigned long long u64;
typedef unsigned int u32;
typedef void *Object;
typedef void *Selector;
typedef struct { float x, y; } Point;
typedef struct { float width, height; } Size;
typedef struct { Point origin; Size size; } Rect;

extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern long write(int, const void *, size_t);
extern long read(int, void *, size_t);
extern int close(int);
extern int pipe(int *);
extern int fork(void);
extern int waitpid(int, int *, int);
extern int getppid(void);
extern void _exit(int);
extern void *mmap(void *, size_t, int, int, int, long long);
extern int munmap(void *, size_t);
extern void *malloc(size_t);
extern void free(void *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
extern int usleep(u32);
extern u64 mach_absolute_time(void);
extern int mach_timebase_info(u32 *);
extern int pthread_create(void **, const void *, void *(*)(void *), void *);
extern int pthread_join(void *, void **);
extern u32 bootstrap_port;
extern int bootstrap_look_up(u32, const char *, u32 *);

/* Integer division: ARMv6 has no divide instruction, and the helpers clang calls for it
 * (__udivdi3 and friends) are in iPhone OS 3's libgcc_s, not libSystem: imported from
 * libSystem they fail to bind at their first call. Defined here, the link binds them locally. */
static u64 udivmod64(u64 n, u64 d, u64 *rem)
{
    u64 q = 0, r = 0;
    for (int i = 63; i >= 0; i--) {
        r = r << 1 | (n >> i & 1);
        if (r >= d) { r -= d; q |= 1ull << i; }
    }
    if (rem) *rem = r;
    return q;
}
u64 __udivdi3(u64 n, u64 d) { return udivmod64(n, d, 0); }
u64 __umoddi3(u64 n, u64 d) { u64 r; udivmod64(n, d, &r); return r; }
u32 __udivsi3(u32 n, u32 d) { return (u32)udivmod64(n, d, 0); }
u32 __umodsi3(u32 n, u32 d) { u64 r; udivmod64(n, d, &r); return (u32)r; }

/* ---------------------------------------------------------------- output --- */

typedef struct __attribute__((packed)) {
    u32 call_number;
    struct __attribute__((packed)) { u32 slot, ctx, argc, spill, args[4]; } gles;   /* 32 bytes */
    long long retval, error;
} QemuCall;
typedef char qemu_call_is_52_bytes[sizeof(QemuCall) == 52 ? 1 : -1];
#define QC_GLES 0x140
#define GLES_OP_LOG 0x1002

static u32 slen(const char *s) { u32 n = 0; while (s[n]) n++; return n; }

/* One line to the engine log (the host prints lines that repeat only a few times,
 * so every result line carries its numbers) and to stderr. */
static void emit(const char *s)
{
    volatile QemuCall q;
    u32 n = slen(s);
    write(2, s, n);
    memset((void *)&q, 0, sizeof(q));
    q.call_number = QC_GLES;
    q.gles.slot = GLES_OP_LOG;
    q.gles.argc = 2;
    q.gles.args[0] = (u32)s;
    q.gles.args[1] = n;
    __asm__ volatile("mcr p15, 3, %0, c15, c15, 0" : : "r"(&q) : "memory");
}

typedef struct { char b[160]; u32 n; } Line;
static void put(Line *l, const char *s) { while (*s && l->n < sizeof(l->b) - 1) l->b[l->n++] = *s++; l->b[l->n] = 0; }
static void putu(Line *l, u64 v)
{
    char t[24], *p = t + 23;
    *p = 0;
    do { *--p = '0' + (char)(v % 10); v /= 10; } while (v);
    put(l, p);
}
static void putx(Line *l, u32 v)
{
    char t[11] = "0x", *p = t + 2;
    for (int i = 7; i >= 0; i--) *p++ = "0123456789abcdef"[(v >> (i * 4)) & 15];
    *p = 0;
    put(l, t);
}
/* v in tenths, printed with one decimal */
static void putd(Line *l, u64 tenths) { char d[2] = {(char)('0' + tenths % 10), 0}; putu(l, tenths / 10); put(l, "."); put(l, d); }

/* --------------------------------------------------------------- timing --- */

static u32 tb[2];
static u64 now_ns(void) { return mach_absolute_time() * tb[0] / tb[1]; }

#ifndef ROUNDS
#define ROUNDS 5
#endif
typedef u64 (*Bench)(u32 n);   /* runs n operations, returns elapsed ns */
typedef struct { const char *name, *what; Bench run; u32 n; } Test;

/* ----------------------------------------------------------- benchmarks --- */

static volatile u32 sink;

/* Plain integer work: the translated-code baseline everything else is measured against. */
static u64 b_loop(u32 n)
{
    u64 t = now_ns();
    u32 x = 1;
    for (u32 i = 0; i < n; i++) x = x * 1664525u + 1013904223u + (x >> 7);
    sink = x;
    return now_ns() - t;
}

/* Calls through a function pointer cycling over four targets: indirect branches,
 * each ending in a return. */
static u32 f0(u32 x) { return x + 1; }
static u32 f1(u32 x) { return x ^ 3; }
static u32 f2(u32 x) { return x * 3; }
static u32 f3(u32 x) { return x - 7; }
static u32 (*volatile table[4])(u32) = {f0, f1, f2, f3};
static u64 b_icall(u32 n)
{
    u64 t = now_ns();
    u32 x = 0;
    for (u32 i = 0; i < n; i++) x = table[i & 3](x);
    sink = x;
    return now_ns() - t;
}

/* Recursion: returns to many different call sites. */
static u32 fib(u32 k) { return k < 2 ? k : fib(k - 1) + fib(k - 2); }
static u64 b_fib(u32 n)   /* n calls, approximately: fib(k) makes 2*fib(k+1)-1 */
{
    u32 k = 2, calls = 1;
    while (calls < n) { k++; calls = 2 * fib(k + 1) - 1; }
    u64 t = now_ns();
    sink = fib(k);
    return (now_ns() - t) * n / calls;
}

static char *buffer_a, *buffer_b;
#define COPY_BYTES (256 * 1024)
static u64 b_memcpy(u32 n)   /* n KiB */
{
    u64 t = now_ns();
    for (u32 done = 0; done < n; done += COPY_BYTES / 1024) memcpy(buffer_b, buffer_a, COPY_BYTES);
    return now_ns() - t;
}

/* A system call that does nothing: SWI, the exception entry and return, kernel entry. */
static u64 b_syscall(u32 n)
{
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) sink = (u32)getppid();
    return now_ns() - t;
}

/* The time base itself (a Mach trap on this OS). */
static u64 b_time(u32 n)
{
    u64 t = now_ns(), x = 0;
    for (u32 i = 0; i < n; i++) x += mach_absolute_time();
    sink = (u32)x;
    return now_ns() - t;
}

/* Ping-pong one byte over two pipes: n round trips, two switches each. Between
 * threads the address space stays; between processes every switch changes ASID. */
static int ping[2], pong[2];
static u32 rounds_wanted, touch_pages;
static char *touch_area;
static void touch(void)
{
    for (u32 p = 0; p < touch_pages; p++) touch_area[p * 4096 + (p & 63) * 64]++;
}
static void echo(void)
{
    char c;
    for (u32 i = 0; i < rounds_wanted; i++) {
        if (read(ping[0], &c, 1) != 1) break;
        touch();
        write(pong[1], &c, 1);
    }
}
static void *echo_thread(void *unused) { (void)unused; echo(); return 0; }
static u64 pingpong(u32 n)
{
    char c = 'x';
    void *thread = 0;
    if (pipe(ping) || pipe(pong)) return 0;
    rounds_wanted = n;
    pthread_create(&thread, 0, echo_thread, 0);
    write(ping[1], &c, 1); read(pong[0], &c, 1);  /* both sides running */
    u64 t = now_ns();
    for (u32 i = 1; i < n; i++) { write(ping[1], &c, 1); touch(); read(pong[0], &c, 1); }
    u64 e = (now_ns() - t) * n / (n - 1);
    pthread_join(thread, 0);
    close(ping[0]); close(ping[1]); close(pong[0]); close(pong[1]);
    return e;
}
static u64 b_thread_switch(u32 n) { touch_pages = 0; return pingpong(n); }
/* The same, each side touching 64 pages per turn: TLB refills without an address-space
 * switch, for comparison with rpc-64p. */
static u64 b_thread_switch_touch(u32 n) { touch_pages = 64; return pingpong(n); }

/* A Mach RPC to launchd and back (bootstrap_look_up of a name it serves): two
 * address-space switches plus launchd's work. An app cannot fork (the sandbox denies
 * it), and talking to other processes this way is what iOS apps do all the time. */
static u64 rpc(u32 n)
{
    u32 port;
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) {
        if (bootstrap_look_up(bootstrap_port, "com.apple.system.notification_center", &port)) return 0;
        touch();
    }
    return now_ns() - t;
}
static u64 b_rpc(u32 n) { touch_pages = 0; return rpc(n); }
/* The same with 64 of this process's pages written between calls: what an address-space
 * switch costs this process in TLB refills (QEMU's TLB is not ASID-tagged). */
static u64 b_rpc_touch(u32 n) { touch_pages = 64; return rpc(n); }

static u64 b_touch(u32 n) { touch_pages = 64; u64 t = now_ns(); for (u32 i = 0; i < n; i++) touch(); return now_ns() - t; }

/* Anonymous memory: n page faults (mapping 256 pages, writing each, unmapping). */
static u64 b_fault(u32 n)
{
    u64 total = 0;
    for (u32 done = 0; done < n; done += 256) {
        u64 t = now_ns();
        char *p = mmap(0, 256 * 4096, 3, 0x1002, -1, 0);
        if (p == (char *)-1) return 0;
        for (u32 i = 0; i < 256; i++) p[i * 4096] = 1;
        munmap(p, 256 * 4096);
        total += now_ns() - t;
    }
    return total;
}

/* mmap + munmap of one page, never touched: the VM system calls alone. */
static u64 b_mmap(u32 n)
{
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) {
        void *p = mmap(0, 4096, 3, 0x1002, -1, 0);
        if (p == (void *)-1) return 0;
        munmap(p, 4096);
    }
    return now_ns() - t;
}

/* Dependent loads chasing a pointer through `pages` pages in a scrambled order:
 * within QEMU's TLB reach, then well beyond it. */
static char *chase_area;
static u64 chase(u32 n, u32 pages)
{
    u32 order[1024];
    for (u32 i = 0; i < pages; i++) order[i] = i;
    u32 seed = 12345;
    for (u32 i = pages - 1; i > 0; i--) {
        seed = seed * 1103515245u + 12345u;
        u32 j = (seed >> 8) % (i + 1), x = order[i];
        order[i] = order[j]; order[j] = x;
    }
    for (u32 i = 0; i < pages; i++) {
        char *from = chase_area + order[i] * 4096 + (order[i] & 63) * 64;
        u32 k = order[(i + 1) % pages];
        *(char **)from = chase_area + k * 4096 + (k & 63) * 64;
    }
    char *p = chase_area + order[0] * 4096 + (order[0] & 63) * 64;
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) p = *(char **)p;
    sink = (u32)p;
    return now_ns() - t;
}
static u64 b_tlb32(u32 n) { return chase(n, 32); }
static u64 b_tlb1024(u32 n) { return chase(n, 1024); }

/* fork, the child exits at once, wait: address-space creation and teardown. */
static u64 b_fork(u32 n)   /* 0: not allowed (an app's sandbox denies fork) */
{
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) {
        int pid = fork();
        if (pid < 0) return 0;
        if (pid == 0) _exit(0);
        waitpid(pid, 0, 0);
    }
    return now_ns() - t;
}

/* usleep(1000): the timer interrupt and wakeup path. Reported as the actual sleep. */
static u64 b_sleep(u32 n)
{
    u64 t = now_ns();
    for (u32 i = 0; i < n; i++) usleep(1000);
    return now_ns() - t;
}

static const Test tests[] = {
    {"loop", "integer loop iteration", b_loop, 4000000},
    {"icall", "indirect call + return", b_icall, 2000000},
    {"fib", "recursive call + return", b_fib, 500000},
    {"memcpy", "KiB copied", b_memcpy, 20480},
    {"syscall", "getppid()", b_syscall, 50000},
    {"time", "mach_absolute_time()", b_time, 50000},
    {"thread-switch", "round trip, 2 thread switches", b_thread_switch, 4000},
    {"thread-switch-64p", "round trip touching 64 pages per side", b_thread_switch_touch, 2000},
    {"rpc", "Mach RPC to launchd and back", b_rpc, 4000},
    {"rpc-64p", "Mach RPC, 64 pages written between calls", b_rpc_touch, 2000},
    {"touch-64p", "writing 64 pages, no switch", b_touch, 20000},
    {"fault", "anonymous page fault (map, write, unmap)", b_fault, 2048},
    {"mmap", "mmap + munmap, untouched", b_mmap, 4000},
    {"tlb-32p", "dependent load, 32-page working set", b_tlb32, 2000000},
    {"tlb-1024p", "dependent load, 1024-page working set", b_tlb1024, 2000000},
    {"fork", "fork + exit + wait", b_fork, 40},
    {"sleep", "usleep(1000), actual", b_sleep, 100},
};
#define NTESTS (sizeof(tests) / sizeof(tests[0]))

static char summary[NTESTS * 48 + 64];
static u32 summary_len;
static int running;

static void run_all(void)
{
    static u32 pass;
    Line l = {{0}, 0};
    u64 started = now_ns();
    pass++;
    summary_len = 0;
    put(&l, "[ltbench] start pass "); putu(&l, pass); put(&l, " rounds "); putu(&l, ROUNDS); put(&l, "\n");
    emit(l.b);
    for (u32 i = 0; i < NTESTS; i++) {
        u64 r[ROUNDS];
        const Test *t = &tests[i];
#ifdef ONLY   /* build.sh with LTB_CFLAGS='-DONLY=\"rpc\" -DROUNDS=50': one test, for profiling */
        const char *a = t->name, *b = ONLY;
        while (*a && *a == *b) a++, b++;
        if (*a || *b) continue;
#endif
        u64 ns = 1;
        for (u32 k = 0; k < ROUNDS && ns; k++) {
            ns = t->run(t->n);
            r[k] = ns * 10 / t->n;   /* tenths of a ns per op */
        }
        if (!ns) {
            l.n = 0;
            put(&l, "[ltbench] "); put(&l, t->name); put(&l, " unavailable\n");
            emit(l.b);
            continue;
        }
        for (u32 a = 0; a < ROUNDS; a++) for (u32 b = a + 1; b < ROUNDS; b++)
            if (r[b] < r[a]) { u64 x = r[a]; r[a] = r[b]; r[b] = x; }
        l.n = 0;
        put(&l, "[ltbench] "); put(&l, t->name); put(&l, " best "); putd(&l, r[0]);
        put(&l, " median "); putd(&l, r[ROUNDS / 2]); put(&l, " ns/op ("); put(&l, t->what);
        put(&l, ", n="); putu(&l, t->n); put(&l, ")\n");
        emit(l.b);
        Line s = {{0}, 0};
        put(&s, t->name); put(&s, " "); putd(&s, r[0]); put(&s, "\n");
        for (u32 c = 0; c < s.n && summary_len < sizeof(summary) - 1; c++) summary[summary_len++] = s.b[c];
        summary[summary_len] = 0;
    }
    l.n = 0;
    put(&l, "[ltbench] done pass "); putu(&l, pass); put(&l, " in "); putu(&l, (now_ns() - started) / 1000000); put(&l, " ms\n");
    emit(l.b);
}

/* ------------------------------------------------------------------ app --- */

static Object (*get_class)(const char *);
static Selector (*get_selector)(const char *);
static void *send;
static Object window, label, delegate_object;

static Object call0(Object object, const char *name) {
    return ((Object (*)(Object, Selector))send)(object, get_selector(name));
}
static Object call1(Object object, const char *name, Object value) {
    return ((Object (*)(Object, Selector, Object))send)(object, get_selector(name), value);
}
static Object string(const char *text) {
    return ((Object (*)(Object, Selector, const char *))send)(get_class("NSString"),
            get_selector("stringWithUTF8String:"), text);
}
static void show(const char *text, const char *color) {
    call1(label, "setText:", string(text));
    call1(window, "setBackgroundColor:", call0(get_class("UIColor"), color));
}

static void finished(Object self, Selector selector, Object unused) {
    (void)self; (void)selector; (void)unused;
    show(summary, "darkGrayColor");
    running = 0;
}
static void *bench_thread(void *unused) {
    (void)unused;
    Object pool = call0(call0(get_class("NSAutoreleasePool"), "alloc"), "init");
    run_all();
    ((void (*)(Object, Selector, Selector, Object, int))send)(delegate_object,
        get_selector("performSelectorOnMainThread:withObject:waitUntilDone:"), get_selector("benchFinished:"), 0, 0);
    call0(pool, "drain");
    return 0;
}
static void start(void) {
    void *thread;
    if (running) return;
    running = 1;
    show("LT Bench\nrunning...\n\nresults go to the engine log", "purpleColor");
    pthread_create(&thread, 0, bench_thread, 0);
}
static void tapped(Object self, Selector selector, Object sender) {
    (void)self; (void)selector; (void)sender;
    start();
}

static void launched(Object self, Selector selector, Object application) {
    (void)selector; (void)application;
    Rect full = {{0, 0}, {320, 480}}, text = {{8, 24}, {304, 448}};
    delegate_object = self;
    emit("[ltbench] launched\n");
    window = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UIWindow"), "alloc"),
              get_selector("initWithFrame:"), full);
    label = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UILabel"), "alloc"),
             get_selector("initWithFrame:"), text);
    call1(label, "setTextColor:", call0(get_class("UIColor"), "whiteColor"));
    call1(label, "setBackgroundColor:", call0(get_class("UIColor"), "clearColor"));
    ((void (*)(Object, Selector, int))send)(label, get_selector("setNumberOfLines:"), 0);
    Object font = ((Object (*)(Object, Selector, float))send)(get_class("UIFont"),
                  get_selector("systemFontOfSize:"), 14.0f);
    call1(label, "setFont:", font);
    call1(window, "addSubview:", label);
    Object button = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UIControl"), "alloc"),
                    get_selector("initWithFrame:"), full);
    ((void (*)(Object, Selector, Object, Selector, u32))send)(button,
        get_selector("addTarget:action:forControlEvents:"), self, get_selector("benchTapped:"), 1u << 6);
    call1(window, "addSubview:", button);
    call0(window, "makeKeyAndVisible");
#ifdef SYMS   /* build.sh with LTB_CFLAGS='-DSYMS=0x33989a1c,0x3386699a': name guest addresses from a profile */
    {
        static const u32 addrs[] = { SYMS };
        typedef struct { const char *fname; void *fbase; const char *sname; void *saddr; } DlInfo;
        int (*dladdr_fn)(const void *, DlInfo *) = dlsym((void *)-2, "dladdr");   /* RTLD_DEFAULT */
        for (u32 i = 0; dladdr_fn && i < sizeof(addrs) / sizeof(addrs[0]); i++) {
            DlInfo info = {0};
            Line l = {{0}, 0};
            put(&l, "[ltbench] sym "); putx(&l, addrs[i]); put(&l, " ");
            if (dladdr_fn((const void *)addrs[i], &info)) {
                const char *f = info.fname ? info.fname : "?", *base = f;
                for (const char *c = f; *c; c++) if (*c == '/') base = c + 1;
                put(&l, base); put(&l, " "); put(&l, info.sname ? info.sname : "?");
                put(&l, "+"); putx(&l, addrs[i] - (u32)info.saddr);
            } else {
                put(&l, "(none)");
            }
            put(&l, "\n");
            emit(l.b);
        }
    }
#endif
    start();
}

int bench_main(int argc, char **argv) {
    mach_timebase_info(tb);
    buffer_a = malloc(COPY_BYTES); buffer_b = malloc(COPY_BYTES);
    touch_area = mmap(0, 64 * 4096, 3, 0x1002, -1, 0);
    chase_area = mmap(0, 1024 * 4096, 3, 0x1002, -1, 0);
    if (!buffer_a || !buffer_b || touch_area == (char *)-1 || chase_area == (char *)-1) { emit("[ltbench] exit: no memory\n"); _exit(10); }
    memset(buffer_a, 1, COPY_BYTES); memset(touch_area, 0, 64 * 4096); memset(chase_area, 0, 1024 * 4096);

    void *runtime = dlopen("/usr/lib/libobjc.A.dylib", 2);
    void *ui = dlopen("/System/Library/Frameworks/UIKit.framework/UIKit", 2);
    if (!runtime || !ui) { emit("[ltbench] exit: no objc/UIKit\n"); _exit(11); }
    get_class = dlsym(runtime, "objc_getClass");
    get_selector = dlsym(runtime, "sel_registerName");
    send = dlsym(runtime, "objc_msgSend");
    Object (*allocate_class)(Object, const char *, u32) = dlsym(runtime, "objc_allocateClassPair");
    int (*add_method)(Object, Selector, void *, const char *) = dlsym(runtime, "class_addMethod");
    void (*register_class)(Object) = dlsym(runtime, "objc_registerClassPair");
    int (*run)(int, char **, Object, Object) = dlsym(ui, "UIApplicationMain");
    if (!get_class || !get_selector || !send || !allocate_class || !add_method || !register_class || !run) { emit("[ltbench] exit: no runtime symbols\n"); _exit(12); }
    Object pool = call0(call0(get_class("NSAutoreleasePool"), "alloc"), "init");
    Object delegate = allocate_class(get_class("NSObject"), "LTBenchDelegate", 0);
    if (!delegate || !add_method(delegate, get_selector("applicationDidFinishLaunching:"), launched, "v@:@") ||
        !add_method(delegate, get_selector("benchTapped:"), tapped, "v@:@") ||
        !add_method(delegate, get_selector("benchFinished:"), finished, "v@:@")) { emit("[ltbench] exit: no delegate class\n"); _exit(13); }
    register_class(delegate);
    int result = run(argc, argv, (Object)0, string("LTBenchDelegate"));
    call0(pool, "drain");
    return result;
}
