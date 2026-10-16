#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Main-thread, single-window presentation adapter. No engine or process ownership. */
typedef struct LTDesktopWindow LTDesktopWindow;
enum LTDesktopEventKind {
    LT_DESKTOP_BUTTON = 1, LT_DESKTOP_TOUCH, LT_DESKTOP_PAUSE,
    LT_DESKTOP_ROTATE, LT_DESKTOP_SCREENSHOT, LT_DESKTOP_STOP,
    LT_DESKTOP_VISIBLE
};
typedef struct LTDesktopEvent {
    int kind;
    int identifier; /* Hardware button or stable guest touch slot. */
    int phase;      /* Button down=1/up=0; touch down=0/move=1/up=2. */
    double x, y;   /* Guest-normalized touch position. */
} LTDesktopEvent;

/* Only an accelerated SDL renderer is admitted; no software fallback. */
LTDesktopWindow *lt_desktop_window_create(const char *title, int width, int height);
const char *lt_desktop_window_error(void);
void lt_desktop_window_destroy(LTDesktopWindow *window);
/* Copies pixels synchronously. Caller may unlock/release the surface on return. */
int lt_desktop_window_frame(LTDesktopWindow *window, const void *bgra,
                            int width, int height, int pitch);
int lt_desktop_window_present(LTDesktopWindow *window, int paused, int stopping);
/* Returns one semantic event; 0 means no more, -1 means invalid input. */
int lt_desktop_window_poll(LTDesktopWindow *window, LTDesktopEvent *event);
/* Cancel emits balanced releases. Drain them before disposing the native link. */
void lt_desktop_window_cancel(LTDesktopWindow *window);
void lt_desktop_window_enabled(LTDesktopWindow *window, int enabled);

#ifdef __cplusplus
}
#endif
