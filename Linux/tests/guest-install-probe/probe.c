/* Self-authored installer/launch acceptance fixture; no Apple SDK headers.
 * Uses public iPhone OS 3.1.3 runtime APIs through their explicit ARM32 ABI.
 * This source has not yet been linked/installed/launched on a real guest.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
typedef void *Object;
typedef void *Selector;
typedef struct { float x, y; } Point;
typedef struct { float width, height; } Size;
typedef struct { Point origin; Size size; } Rect;
extern void *dlopen(const char *, int);
extern void *dlsym(void *, const char *);
extern long write(int, const void *, unsigned long);
extern void _exit(int);

static Object (*get_class)(const char *);
static Selector (*get_selector)(const char *);
static void *send;
static Object window;
static Object label;
static int taps;
static char title[] = "LightTouch\nInstall probe\nLAUNCH CONFIRMED\nTap to verify input";

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
static void log_line(const char *line, unsigned length) { (void)write(2, line, length); }

/* Touch-up inside a real UIControl: no private guest-agent input path. */
static void tapped(Object self, Selector selector, Object sender) {
    (void)self; (void)selector; (void)sender;
    taps++;
    call1(label, "setText:", string(taps & 1 ? "LightTouch\nInstall probe\nINPUT CONFIRMED\nTap again" : title));
    call1(window, "setBackgroundColor:", call0(get_class("UIColor"), taps & 1 ? "blueColor" : "purpleColor"));
    log_line("LT_INSTALL_PROBE_TOUCH\n", sizeof("LT_INSTALL_PROBE_TOUCH\n") - 1);
}

static void launched(Object self, Selector selector, Object application) {
    (void)selector; (void)application;
    Rect full = {{0, 0}, {320, 480}}, text = {{16, 90}, {288, 280}};
    window = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UIWindow"), "alloc"),
              get_selector("initWithFrame:"), full);
    call1(window, "setBackgroundColor:", call0(get_class("UIColor"), "purpleColor"));
    label = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UILabel"), "alloc"),
             get_selector("initWithFrame:"), text);
    call1(label, "setText:", string(title));
    call1(label, "setTextColor:", call0(get_class("UIColor"), "whiteColor"));
    call1(label, "setBackgroundColor:", call0(get_class("UIColor"), "clearColor"));
    ((void (*)(Object, Selector, int))send)(label, get_selector("setNumberOfLines:"), 0);
    ((void (*)(Object, Selector, int))send)(label, get_selector("setTextAlignment:"), 1);
    Object font = ((Object (*)(Object, Selector, float))send)(get_class("UIFont"),
                  get_selector("boldSystemFontOfSize:"), 22.0f);
    call1(label, "setFont:", font);
    call1(window, "addSubview:", label);
    Object button = ((Object (*)(Object, Selector, Rect))send)(call0(get_class("UIControl"), "alloc"),
                    get_selector("initWithFrame:"), full);
    ((void (*)(Object, Selector, Object, Selector, unsigned))send)(button,
        get_selector("addTarget:action:forControlEvents:"), self, get_selector("probeTapped:"), 1u << 6);
    call1(window, "addSubview:", button);
    call0(window, "makeKeyAndVisible");
    log_line("LT_INSTALL_PROBE_LAUNCHED\n", sizeof("LT_INSTALL_PROBE_LAUNCHED\n") - 1);
}

int probe_main(int argc, char **argv) {
    void *runtime = dlopen("/usr/lib/libobjc.A.dylib", 2);
    void *ui = dlopen("/System/Library/Frameworks/UIKit.framework/UIKit", 2);
    if (!runtime || !ui) _exit(11);
    get_class = dlsym(runtime, "objc_getClass");
    get_selector = dlsym(runtime, "sel_registerName");
    send = dlsym(runtime, "objc_msgSend");
    Object (*allocate_class)(Object, const char *, unsigned) = dlsym(runtime, "objc_allocateClassPair");
    int (*add_method)(Object, Selector, void *, const char *) = dlsym(runtime, "class_addMethod");
    void (*register_class)(Object) = dlsym(runtime, "objc_registerClassPair");
    int (*run)(int, char **, Object, Object) = dlsym(ui, "UIApplicationMain");
    if (!get_class || !get_selector || !send || !allocate_class || !add_method || !register_class || !run) _exit(12);
    Object pool = call0(call0(get_class("NSAutoreleasePool"), "alloc"), "init");
    Object delegate = allocate_class(get_class("NSObject"), "LTInstallProbeDelegate", 0);
    if (!delegate || !add_method(delegate, get_selector("applicationDidFinishLaunching:"), launched, "v@:@") ||
        !add_method(delegate, get_selector("probeTapped:"), tapped, "v@:@")) _exit(13);
    register_class(delegate);
    int result = run(argc, argv, (Object)0, string("LTInstallProbeDelegate"));
    call0(pool, "drain");
    return result;
}
