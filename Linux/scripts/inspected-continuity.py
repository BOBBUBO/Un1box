#!/usr/bin/env python3
"""Admission policy for a caller-inspected image, not automatic scene detection.

The adapter owns process/window checks, bounded native captures and balanced
input. The original inspection receipt is never modified. No Home/editor gate
or deadline epoch is created here.
"""

REUSE_NS = 60_000_000_000
CAPTURE_BUDGET_NS = 12_000_000_000
SOURCE_AGE_NS = 2_000_000_000
DIAGNOSTICS_AGE_NS = 1_000_000_000


class Rejected(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def recent(now, timestamp, limit):
    return 0 <= now - int(timestamp) <= limit


def identity_matches(gate, owner):
    return (gate['frontend_pid'] == owner['frontend_pid']
            and gate['start_ticks'] == owner['start_ticks'])


def admit_rewake(gate, inspected_bytes, owner, *, now, status, pulse,
                 capture, verify_window, wait, compare_scene=None):
    """Return fresh known-scene continuity or reject before contact input.

    verify_window returns a validated viewport and must independently check the
    live PID/startticks/UID/argv, window PID and focus on every invocation.
    capture(deadline_ns) returns (native BMP bytes, flat receipt, saved identifier).
    It must enforce that absolute deadline in its waits/subprocesses and retain
    every actual capture, including rejected black/changed ones. Defer image
    conversion until input admission finishes. pulse sends one balanced
    150ms Power press. compare_scene optionally provides an explicit reviewed
    stable-region policy for this already-inspected Lock scene. It never infers
    a new scene. Unmatched frames stay retained while boundedly awaiting matching
    anchors; the adapter immediately admits its gesture on return.
    """
    start = now()
    require(gate['model_inspected'] is True and gate['scene'] == 'Lock Screen',
            'actual model-inspected Lock Screen required')
    require(identity_matches(gate, owner), 'inspection owner changed')
    require(recent(start, gate['inspected_monotonic_ns'], REUSE_NS),
            'original inspection exceeded 60s; inspect again')
    require(bool(inspected_bytes), 'empty inspected BMP')
    viewport = verify_window()
    current = status()
    require(int(current['state']) == 2
            and recent(now(), current['observed_monotonic_ns'], DIAGNOSTICS_AGE_NS),
            'stale/non-running current status')
    require(int(current['sleeping']) in (0, 1), 'invalid sleep status')
    pulsed = int(current['sleeping']) == 1
    if pulsed:
        pulse()  # Exactly one press; an awake screen must not be put to sleep.
    deadline = start + CAPTURE_BUDGET_NS
    while True:
        require(now() < deadline, '12s capture budget exceeded')
        require(recent(now(), gate['inspected_monotonic_ns'], REUSE_NS),
                'original inspection exceeded 60s; inspect again')
        require(verify_window() == viewport, 'viewport changed')
        data, receipt, saved_capture = capture(deadline)
        finished = now()
        require(finished <= deadline, '12s capture budget exceeded')
        require(recent(finished, gate['inspected_monotonic_ns'], REUSE_NS),
                'original inspection exceeded 60s; inspect again')
        require(verify_window() == viewport, 'viewport changed')
        # During a rewake the first native capture can still be asleep. Keep
        # it, boundedly await actual awake diagnostics, and never infer a scene.
        awake = (int(receipt['sleeping']) == 0 and int(receipt['agent']) == 1
                 and int(receipt['backlight']) > 0)
        if not awake:
            wait(.5 if compare_scene else .02)
            continue
        comparison = (compare_scene(inspected_bytes, data) if compare_scene else
                      {'matched': data == inspected_bytes, 'policy': 'legacy-full-BMP'})
        if compare_scene and not comparison['matched']:
            wait(.5)  # At most2Hz; no unbounded fade/animation capture storm.
            continue
        require(comparison['matched'], 'awake BMP changed; inspect the retained new image')
        require(recent(finished, receipt['observed_monotonic_ns'], SOURCE_AGE_NS)
                and recent(finished, receipt['frame_monotonic_ns'], SOURCE_AGE_NS),
                'stale current frame/capture')
        require(recent(finished, receipt['diagnostics_monotonic_ns'], DIAGNOSTICS_AGE_NS),
                'stale current diagnostics')
        # Revalidate after all potentially blocking adapter operations; no
        # conversion, watcher wait or model roundtrip belongs before gesture.
        current = status()
        require(int(current['state']) == 2 and int(current['sleeping']) == 0
                and int(current['agent']) == 1 and int(current['backlight']) > 0
                and recent(now(), current['observed_monotonic_ns'], DIAGNOSTICS_AGE_NS),
                'display/status changed before gesture')
        final = now()
        require(final <= deadline
                and recent(final, gate['inspected_monotonic_ns'], REUSE_NS)
                and recent(final, receipt['frame_monotonic_ns'], SOURCE_AGE_NS)
                and recent(final, receipt['diagnostics_monotonic_ns'], DIAGNOSTICS_AGE_NS),
                'admission aged before gesture')
        return {'saved_capture': saved_capture, 'viewport': viewport,
                'pulsed': pulsed, 'original_inspected_monotonic_ns': gate['inspected_monotonic_ns'],
                'admitted_monotonic_ns': final, 'byte_identical': data == inspected_bytes,
                'comparison': comparison,
                'no_new_scene_asserted': True, 'guest_acceptance': False}
