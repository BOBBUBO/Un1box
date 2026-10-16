# Poco concurrent-media stall: diagnosis and proposed successor

Test32's 30-second keyboard pilot failed the required concurrent interval
assertion. Forty input requests and nonzero PCM continued, while produced and
presented frame counters advanced four times and then stayed constant. Initial
native captures showed the scratch Note and keyboard. No native end frame was
captured because the assertion preceded the normal end capture. A confirmed
clean Stop followed. This is a retained failure, not a concurrency pass.

The failure handler now attempts status, the latest published native frame, and
another status before the worker binding and ActivityScenario close. Each
attempt runs even if another fails; diagnostic exceptions are suppressed onto
the original failure. These captures do not force a fresh LCD scanout or prove
which pixels the guest compositor most recently produced. No extra input,
retry, pause, resume, or deadline extension is introduced.

## What the source establishes

The installed engine revision is `96f2d08edd1439ba2b41624190ee68b8a7851625`.

* `Portable/src/Runtime.cpp` counts produced frames after acquisition of a new
  engine frame serial. Android's presenter subsequently counts presentations.
  Both stopping places the fault at or before engine acquisition, rather than
  establishing a Surface posting failure.
* `contrib/ios-app/qemu-ios-ui.c:ios_capture` compares every visible pixel with
  the last published buffer and retains the serial for identical pixels.
  `ios_gfx_update` marks a pending paint and schedules deferred publication on
  portable hosts. `ios_refresh` also drives the LCD and publishes pending work.
* `hw/arm/ipod_touch_lcd.c:lcd_refresh` reads the latched scanout, tracks dirty
  memory and GLES generations, and emits dirty updates. Vblank drives a scan
  when the console is visible; the display refresh is a further scan path.
* The retained SPI trace shows touch frames were read. It does not establish
  that UIKit consumed the keys or that Notes rendered the expected characters.
  Coordinates match the earlier manual Q, and actual DOWN/UP dwell was about
  78–87 ms; a missing hold is not demonstrated.

Identical-pixel suppression can hide a transient Q followed by backspace if
both paints complete before the next capture/publication and the final pixels
match the old buffer. A 250 ms host delay does not guarantee an intermediate
guest paint on a busy emulator. Conversely, a changed completed panel paint
that reaches `ios_capture` cannot be discarded as identical. Deferred portable
publication is intended to retain complete paints. Suppression therefore
remains a hypothesis; it does not explain away 27 seconds of stalled counters
or justify aggregating wider intervals into a pass.

## Proposed visible-phase pilot (not implemented or authorized to run)

Keep a 30-second pilot, the same reviewed scratch Note/worker/image binding,
balanced 60 ms DOWN/UP events, absolute cutoffs, and the existing required
input + produced frame + presented frame + nonzero pull/write + playback
overlap within each declared block. Keep lifecycle and strict clean Stop.
Do not reuse the spent worker or mutate the protected original.

1. Capture the initial native Note, keyboard anchors and status. Send one Q
   DOWN/UP. Leave the letter visible for a fixed 1,500 ms without backspace.
2. Capture native pixels and status **before** compensation, including input,
   serial/frame and audio deltas. Recheck the same worker, Surface, keyboard
   anchors and no held contacts. Retain each phase's raw times and images.
   Visual review must establish the expected added Q before using a later
   compensation step; counter progress alone does not establish Note content.
3. Only on the verified scene, send balanced backspace, wait another fixed
   1,500 ms, and capture the restored Note. Do not catch up missed phases or
   extend the original cutoff. A changed/unverified scene fails and stops;
   no guessed extra backspace is allowed.
4. Record the original overlap predicate on the same phase samples. A visible
   phase does not relax the requirement or qualify sustained 20-minute media.

If the published images still stagnate, an engine diagnostic successor should
record bounded counters at LCD scan/dirty-update, `ios_capture` attempts,
identical suppression, published serial, and portable acquisition. Record the
latched scanout/base and GLES generation at those boundaries. Comparing these
stages distinguishes unchanged guest scanout, missed dirty notification,
deferred publication, and consumer acquisition. A latest-published screenshot
alone cannot make that distinction. No product rendering bypass or forced
duplicate frames should be used to manufacture frame progress.

The new exception-preservation helper has a host-JVM contract test. Full APK
compilation and actual target exercise remain pending separate allocation.
