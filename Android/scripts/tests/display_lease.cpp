#include "../../app/src/main/cpp/display_lease.h"
#include <cassert>
#include <iostream>

namespace {
bool guarded=false;
struct Window { int refs=1,releases=0; };
void release(Window *window) { assert(!guarded);assert(window->refs>0);--window->refs;++window->releases; }
using Converted=ConvertedWindow<Window,release>;
void obsolete_conversion_and_release() {
    DisplayLease state;Window old, replacement;Window *active=nullptr;
    const auto a=state.claim();const auto pending_a=state.begin(a);
    {
        Converted incoming(&old); // A's conversion returns after B took over.
        const auto b=state.claim();const auto pending_b=state.begin(b);
        { Converted next(&replacement);guarded=true;
          assert(state.current(pending_b));active=next.take();guarded=false; }
        guarded=true;
        assert(!state.current(pending_a));assert(!state.release(a));assert(state.owns(b));
        guarded=false;
    }
    assert(old.refs==0 && old.releases==1);assert(active==&replacement && replacement.refs==1);
    release(active);assert(replacement.refs==0 && replacement.releases==1);
}
void retired_controls_and_duplicate_callbacks() {
    DisplayLease state;const auto a=state.claim();const auto b=state.claim();
    int native_cancel=0,native_pause=0,native_touch=0;
    // These are the same ownership predicates used inside JNI's guard.
    if(state.owns(a)){++native_cancel;++native_pause;++native_touch;}
    assert(native_cancel==0 && native_pause==0 && native_touch==0);
    assert(state.begin(a).owner==0);assert(!state.release(a));
    auto first=state.begin(b);auto duplicate=state.begin(b);
    assert(!state.current(first));assert(state.current(duplicate));
    assert(state.release(b));assert(!state.release(b));assert(!state.current(duplicate));
}
void no_contact_attach_preserves_buttons_and_held_takeover_requires_cancel() {
    DisplayLease state;state.claim();bool button_held=true;int cancellations=0;
    auto takeover=[&]{ if(state.contactsHeld()){++cancellations;button_held=false;state.contactsReleased();}return state.claim(); };
    auto b=takeover();assert(cancellations==0 && button_held);
    state.begin(b);state.begin(b);assert(!state.contactsHeld() && button_held);
    state.admittedContact(7,0);state.admittedContact(23,0);state.admittedContact(7,1);
    state.admittedContact(7,2);assert(state.contactsHeld());
    takeover();assert(cancellations==1 && !button_held && !state.contactsHeld());
    state.admittedContact(7,0);state.admittedContact(7,2);button_held=true;
    takeover();assert(cancellations==1 && button_held);
}
}
int main() {
    obsolete_conversion_and_release();retired_controls_and_duplicate_callbacks();
    no_contact_attach_preserves_buttons_and_held_takeover_requires_cancel();
    std::cout<<"PASS display lease stale controls/conversion, converted refs, contact handoff and empty callback pulse preservation\n";
}
