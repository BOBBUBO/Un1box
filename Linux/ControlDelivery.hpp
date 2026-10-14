#pragma once
namespace lt {
/* One pending cancellation, no unbounded input retry queue. A dropped release
 * may leave a guest contact/button held; enqueue cancellation before admitting
 * any newer input. IPC admission still does not claim engine/guest delivery. */
class ControlDelivery {
    bool cancellation_pending = false;

  public:
    enum class Kind { input, cancel, pause, lifecycle };
    bool needs_cancel() const { return cancellation_pending; }
    template <class Sender> bool send(Kind kind, Sender sender) {
        if (cancellation_pending && kind == Kind::input)
            return false;
        bool sent = sender();
        if (!sent && kind != Kind::lifecycle)
            cancellation_pending = true;
        if (sent && kind == Kind::cancel)
            cancellation_pending = false;
        return sent;
    }
    template <class Sender> void retry_cancel(Sender sender) {
        if (cancellation_pending && sender())
            cancellation_pending = false;
    }
};
} // namespace lt
