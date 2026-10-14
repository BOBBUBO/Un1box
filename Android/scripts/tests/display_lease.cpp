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
void same_worker_foreground_return_requires_fresh_native_token() {
    DisplayLease state;const auto a=state.claim();auto pending=state.begin(a);
    const auto b=state.claim();assert(!state.owns(a));
    const auto returning_a=state.claim();assert(returning_a>a && returning_a>b);
    assert(!state.current(pending));assert(!state.release(a));assert(!state.detach(b));
    assert(state.owns(returning_a) && state.current(state.begin(returning_a)));
}
void surface_loss_keeps_background_authority_but_retired_owner_cannot_pause() {
    DisplayLease state;const auto a=state.claim();auto pending=state.begin(a);
    assert(state.detach(a));assert(!state.current(pending));assert(state.owns(a));
    int pauses=0;if(state.owns(a))++pauses;assert(pauses==1); // Surface destroy then Activity stop.
    assert(state.current(state.begin(a))); // Same Activity can reattach without minting.
    const auto b=state.claim();
    assert(!state.detach(a));if(state.owns(a))++pauses;
    assert(pauses==1 && state.owns(b));assert(!state.release(a));
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
void admitted_cancel_releases_only_its_id_and_last_cancel_preserves_new_pulse() {
    DisplayLease state;auto a=state.claim();
    state.admittedContact(7,0);state.admittedContact(23,0);
    state.admittedContact(7,3);assert(state.contactsHeld()); // ID23 remains held.
    state.admittedContact(23,3);assert(!state.contactsHeld());
    bool power_held=true;int cancellations=0;
    // A new unrelated pulse follows the successful CANCEL admissions.
    if(state.contactsHeld()){++cancellations;power_held=false;}
    assert(state.detach(a));state.claim();
    assert(cancellations==0 && power_held && !state.contactsHeld());
}
void delayed_rejection_cannot_release_new_owner_but_current_rejected_up_can() {
    DisplayLease state;auto a=state.claim();
    state.admittedContact(7,0);state.admittedContact(7,2);
    state.submittedInput(41,a,true); // A's queued UP may still fail with an empty ledger.
    const auto b=state.claim();bool power_held=true;int cancellations=0;
    assert(!state.reportsRejectedInput(41));
    if(state.cancelsRejectedInput(41)){++cancellations;power_held=false;state.contactsReleased();}
    assert(cancellations==0 && power_held && state.owns(b));
    state.submittedInput(42,b,false); // Current B admitted no held contacts.
    assert(state.reportsRejectedInput(42));assert(!state.cancelsRejectedInput(42));
    assert(power_held);state.inputCompleted(42);
    state.admittedContact(23,0);state.submittedInput(43,b,true);
    assert(!state.reportsRejectedInput(41));assert(state.cancelsRejectedInput(43));
    state.admittedContact(23,2);state.submittedInput(44,b,true);
    assert(!state.contactsHeld());assert(state.cancelsRejectedInput(44));
    state.contactsReleased();assert(!state.reportsRejectedInput(44)); // No duplicate cancel after a full release.
    state.submittedInput(45,b,false);state.inputCompleted(45);
    assert(!state.reportsRejectedInput(45)); // Terminal success discharges delayed cleanup.
    state.submittedInput(46,0,true);state.claim();
    assert(state.reportsRejectedInput(46) && state.cancelsRejectedInput(46)); // Explicit legacy/global input.
}
void pending_release_is_balanced_before_handoff_or_detach_and_completed_release_is_not_cancelled() {
    for(const int phase:{2,3})for(const bool detach:{false,true}) {
        DisplayLease state;const auto a=state.claim();bool guest_contact_held=true;int cancellations=0;
        state.admittedContact(7,0);state.admittedContact(7,phase);
        state.submittedInput(71,a,true); // Guest DOWN delivered, release queued but not delivered.
        assert(!state.contactsHeld() && state.pendingOwnedRelease());
        if(state.needsOwnedRelease()){++cancellations;guest_contact_held=false;state.contactsReleased();}
        if(detach)assert(state.detach(a));
        const auto b=state.claim();bool power_held=true;
        assert(!guest_contact_held && cancellations==1 && state.owns(b));
        // The queued old release now fails; it cannot issue a second cancellation.
        assert(!state.reportsRejectedInput(71) && !state.cancelsRejectedInput(71));
        assert(power_held && cancellations==1);
    }
    DisplayLease state;auto a=state.claim();state.admittedContact(7,0);state.admittedContact(7,2);
    state.submittedInput(72,a,true);bool guest_contact_held=false,power_held=true;
    state.inputCompleted(72); // The pre-handoff status refresh observes terminal success.
    assert(!state.needsOwnedRelease());assert(state.detach(a));state.claim();
    assert(!guest_contact_held && power_held && !state.reportsRejectedInput(72));
}
}
int main() {
    obsolete_conversion_and_release();retired_controls_and_duplicate_callbacks();
    surface_loss_keeps_background_authority_but_retired_owner_cannot_pause();
    same_worker_foreground_return_requires_fresh_native_token();
    no_contact_attach_preserves_buttons_and_held_takeover_requires_cancel();
    admitted_cancel_releases_only_its_id_and_last_cancel_preserves_new_pulse();
    delayed_rejection_cannot_release_new_owner_but_current_rejected_up_can();
    pending_release_is_balanced_before_handoff_or_detach_and_completed_release_is_not_cancelled();
    std::cout<<"PASS display lease stale controls/conversion, converted refs, contact handoff and empty callback pulse preservation\n";
}
