#pragma once
#include "lighttouch.h"
#if defined(__linux__) && !defined(__ANDROID__)
namespace lt {
// Private Linux CLI instrumentation. Enables one fixed existing engine event
// before start; no caller event/path/argv and no product/header ABI extension.
lt_status linux_touch_trace_enable(lt_runtime *, lt_error *);
} // namespace lt
#endif
