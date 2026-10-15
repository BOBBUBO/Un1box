#!/usr/bin/env python3
"""Request ordinary Stop unless reviewed scene prerequisites precede fixed RPCs."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import struct
import time


SCENES = {
    'home': ('home-gate.json', 'Home Screen', 129),
    'editor': ('editor-gate.json', 'Notes focused new editor', 179),
    'written-marker': ('written-marker-gate.json', 'Notes written marker', 260),
    'saved-marker': ('saved-marker-gate.json', 'Notes saved marker', 179),
}

# Native portrait n72 status-bar time only. Wi-Fi, battery, all application
# pixels and the complete dock remain protected. This never identifies Home;
# the caller must already have inspected the original full image as Home.
HOME_CLOCK_BOX = (129, 1, 190, 19)
# Explicit operator assertion of a blank focused n72 editor is required.
# Only RGB in this exact insertion-caret rectangle may vary during its fade.
EDITOR_CARET_BOX = (32, 104, 35, 126)


def editor_caret_continuity(original, current, result):
    """Explicit caret and status-clock RGB tolerance; never scene recognition."""
    result.update(policy='n72-explicit-blank-editor-caret-clock-RGB-v3', matched=False,
                  caret_box=list(EDITOR_CARET_BOX), excluded_clock_box=list(HOME_CLOCK_BOX),
                  tolerated_RGB_pixels=66, tolerated_RGB_bytes=198,
                  all_alpha_protected=True, no_caret_shape_or_color_recognition=True)
    try:
        heights = []
        for data in (original, current):
            if len(data) != 54 + 320 * 480 * 4 or data[:2] != b'BM':
                return result
            size, = struct.unpack_from('<I', data, 2)
            offset, = struct.unpack_from('<I', data, 10)
            dib, width, height, planes, bits, compression = struct.unpack_from('<IiiHHI', data, 14)
            if (size != len(data) or offset != 54 or dib != 40 or width != 320 or
                    abs(height) != 480 or planes != 1 or bits != 32 or compression != 0):
                return result
            heights.append(height)
        if original[:54] != current[:54] or original[57::4] != current[57::4]:
            return result
        x1, y1, x2, y2 = EDITOR_CARET_BOX
        clock_x1, clock_y1, clock_x2, clock_y2 = HOME_CLOCK_BOX

        def parts(data, height):
            protected, caret = [], []
            for y in range(480):
                offset = 54 + (y if height < 0 else 479 - y) * 320 * 4
                row = data[offset:offset + 320 * 4]
                if clock_y1 <= y < clock_y2:
                    protected.append(row[:clock_x1 * 4] + row[clock_x2 * 4:])
                elif y1 <= y < y2:
                    caret.append(row[x1 * 4:x2 * 4])
                    protected.append(row[:x1 * 4] + row[x2 * 4:])
                else:
                    protected.append(row)
            return b''.join(protected), hashlib.sha256(b''.join(caret)).hexdigest()

        old, old_caret = parts(original, heights[0])
        new, new_caret = parts(current, heights[1])
        result.update(caret_original_sha256=old_caret, caret_current_sha256=new_caret,
                      protected_original_sha256=hashlib.sha256(old).hexdigest(),
                      protected_current_sha256=hashlib.sha256(new).hexdigest(),
                      matched=old == new)
    except (ValueError, struct.error):
        pass
    return result


def image_continuity(original, current, scene, blank_focused_editor=False):
    """Exact bytes, or explicit Home clock/blank-editor 66-pixel RGB tolerance."""
    result = {'policy': 'full-BMP-v1', 'matched': bool(original) and original == current,
              'full_BMP_identical': original == current, 'no_new_scene_asserted': True}
    if scene == 'Notes focused new editor' and blank_focused_editor is True:
        return editor_caret_continuity(original, current, result)
    if result['matched']:
        return result
    if scene != 'Home Screen':
        return result
    result.update(policy='n72-Home-clock-only-v1', excluded_clock_box=list(HOME_CLOCK_BOX))
    try:
        heights = []
        for data in (original, current):
            if len(data) != 54 + 320 * 480 * 4 or data[:2] != b'BM':
                return result
            size, = struct.unpack_from('<I', data, 2)
            offset, = struct.unpack_from('<I', data, 10)
            dib, width, height, planes, bits, compression = struct.unpack_from('<IiiHHI', data, 14)
            if (size != len(data) or offset != 54 or dib != 40 or width != 320 or
                    abs(height) != 480 or planes != 1 or bits != 32 or compression != 0):
                return result
            heights.append(height)
        if original[:54] != current[:54] or original[57::4] != current[57::4]:
            return result
        x1, y1, x2, y2 = HOME_CLOCK_BOX
        def protected(data, height):
            rows = []
            for y in range(480):
                offset = 54 + (y if height < 0 else 479 - y) * 320 * 4
                row = data[offset:offset + 320 * 4]
                rows.append(row[:x1 * 4] + row[x2 * 4:] if y1 <= y < y2 else row)
            return b''.join(rows)
        old, new = protected(original, heights[0]), protected(current, heights[1])
        result.update(matched=old == new, protected_original_sha256=hashlib.sha256(old).hexdigest(),
                      protected_current_sha256=hashlib.sha256(new).hexdigest())
    except (ValueError, struct.error):
        pass
    return result

def valid_gate(gate, receipt, identity, scene, deadline_ns, now_ns):
    """Human/model scene inspection is supplied; counters never create a gate."""
    try:
        capture = int(receipt['observed_monotonic_ns'])
        source = int(receipt['frame_monotonic_ns'])
        diagnostics = int(receipt['diagnostics_monotonic_ns'])
        inspected = gate['inspected_monotonic_ns']
        return (gate['model_inspected'] is True and gate['scene'] == scene and
                all(gate[k] == identity[k] for k in identity) and
                identity['hello_monotonic_ns'] <= capture <= inspected <= deadline_ns and inspected <= now_ns and
                0 <= inspected - capture <= 5_000_000_000 and
                0 <= capture - source <= 2_000_000_000 and
                0 <= capture - diagnostics <= 1_000_000_000 and
                int(receipt['sequence']) > 1 and int(receipt['sleeping']) == 0 and
                int(receipt['agent']) == 1 and int(receipt['backlight']) > 0)
    except (KeyError, TypeError, ValueError):
        return False


def valid_continuity_gate(gate, original, current, original_bmp, current_bmp,
                          identity, scene, deadline_ns, now_ns):
    """An actual prior scene inspection plus separately timed exact continuity.

    This records a past admission, not a new model inspection. Its verification
    must precede the original deadline; later watchdog polls do not age or reset
    that already-admitted gate. Static publication is permitted only with fresh
    capture/diagnostics and exact same-worker coherent copy readback. Independently sampled Status
    sequences are deliberately not equated with the copied sequence. Publication
    timestamps use host CLOCK_MONOTONIC; they do not measure observation age.
    The loader supplies actual bounded file bytes.
    """
    try:
        proof = gate['continuity']
        inspected = int(gate['inspected_monotonic_ns'])
        verified = int(proof['verified_monotonic_ns'])
        captured = int(current['observed_monotonic_ns'])
        checked = int(proof['owner_checked_monotonic_ns'])
        status = proof['status']
        viewport = proof['viewport']
        readback = proof['metadata_readback']
        blank_editor = gate.get('blank_focused_editor_model_assertion', False)
        comparison = image_continuity(original_bmp, current_bmp, scene, blank_editor)
        return (proof['policy'] == 'coherent-copy-static-awake-v2' and
                gate['model_inspected'] is True and gate['scene'] == scene and
                type(blank_editor) is bool and
                proof.get('blank_focused_editor_model_assertion', False) == blank_editor and
                (not blank_editor or scene == 'Notes focused new editor') and
                all(gate[k] == proof[k] == identity[k] for k in identity) and
                gate['worker_pid'] == proof['worker_pid'] and
                gate['worker_start_ticks'] == proof['worker_start_ticks'] and
                isinstance(proof['worker_pid'], int) and proof['worker_pid'] > 0 and
                proof['worker_pid'] != identity['frontend_pid'] and bool(proof['worker_start_ticks']) and
                identity['hello_monotonic_ns'] <= int(original['observed_monotonic_ns']) <=
                inspected <= captured <= checked <= verified <= deadline_ns and verified <= now_ns and
                0 <= verified - inspected <= 60_000_000_000 and
                comparison['matched'] and (original_bmp == current_bmp or
                    proof.get('image_comparison') == comparison) and
                all(int(r['sequence']) > 1 and int(r['sleeping']) == 0 and
                    int(r['agent']) == 1 and int(r['backlight']) > 0 for r in (original, current)) and
                0 < int(original['frame_monotonic_ns']) <= int(original['observed_monotonic_ns']) and
                0 <= int(original['observed_monotonic_ns']) - int(original['diagnostics_monotonic_ns']) <= 1_000_000_000 and
                0 <= verified - captured <= 2_000_000_000 and
                int(current['sequence']) >= int(original['sequence']) and
                0 < int(original['frame_monotonic_ns']) <= int(current['frame_monotonic_ns']) <= captured and
                coherent_copy(current, identity['hello_monotonic_ns'], verified) and
                captured <= int(current['saved_monotonic_ns']) <= checked and
                int(readback['sequence']) == int(current['sequence']) and
                int(readback['frame_monotonic_ns']) == int(current['frame_monotonic_ns']) and
                int(readback['shared_generation']) == int(current['shared_generation']) and
                0 <= verified - int(current['diagnostics_monotonic_ns']) <= 1_000_000_000 and
                0 <= verified - int(status['observed_monotonic_ns']) <= 1_000_000_000 and
                int(status['state']) == 2 and int(status['sleeping']) == 0 and
                int(status['agent']) == 1 and int(status['backlight']) > 0 and
                isinstance(proof['window'], int) and proof['window'] > 0 and
                gate['window'] == proof['window'] and gate['viewport'] == viewport and
                len(viewport) == 2 and all(isinstance(v, int) and 0 < v <= 16384 for v in viewport))
    except (KeyError, TypeError, ValueError):
        return False


def capture_attempts(now, wait, deadline):
    """At most2Hz, fixed operation deadline; callback work consumes the budget."""
    attempt = 0
    due = now()
    while now() < deadline:
        if now() < due:
            wait(max(0, min(due, deadline) - now()) / 1e9)
        if now() >= deadline:
            return
        due = now() + 500_000_000
        attempt += 1
        yield attempt


def coherent_copy(receipt, hello, verified):
    """Exact native pixels/metadata share one accepted even generation, not Status sampling."""
    try:
        start = int(receipt['copy_started_monotonic_ns'])
        finish = int(receipt['copy_finished_monotonic_ns'])
        observed = int(receipt['observed_monotonic_ns'])
        generation = int(receipt['shared_generation'])
        return (int(receipt['coherent_readback']) == 1 and generation > 0 and generation % 2 == 0 and
                hello <= start <= finish <= observed <= verified and
                0 <= verified - start <= 2_000_000_000)
    except (KeyError, ValueError, TypeError):
        return False


def valid_scene_transition(before, after, before_bmp, after_bmp):
    """Actual changed pixels and a newer source after input, never a scene label."""
    try:
        return (bool(before_bmp) and bool(after_bmp) and before_bmp != after_bmp and
                int(after['sequence']) > int(before['sequence']) > 1 and
                int(after['observed_monotonic_ns']) > int(before['observed_monotonic_ns']) and
                0 < int(before['frame_monotonic_ns']) <= int(after['frame_monotonic_ns']) <=
                int(after['observed_monotonic_ns']))
    except (KeyError, TypeError, ValueError):
        return False


def read_capture(root, description):
    path = Path(description['capture_directory']).resolve()
    if root not in path.parents:
        raise ValueError('capture outside run')
    if (path / 'receipt.json').stat().st_size > 16_384 or (path / 'native.bmp').stat().st_size > 64 * 1024 * 1024 + 54:
        raise ValueError('capture bounds')
    receipt_bytes = (path / 'receipt.json').read_bytes()
    bitmap = (path / 'native.bmp').read_bytes()
    if (hashlib.sha256(receipt_bytes).hexdigest() != description['receipt_sha256'] or
            hashlib.sha256(bitmap).hexdigest() != description['bmp_sha256']):
        raise ValueError('capture hashes changed')
    receipt = json.loads(receipt_bytes)
    return receipt.get('receipt', receipt), bitmap


def load_gate(root, name, identity, scene, deadline_ns, now_ns, expected_marker=None):
    try:
        gate = json.loads((root / name).read_text())
        if gate.get('proof_kind') == 'root-vetted-n72-lock-home-v1':
            if scene != 'Home Screen' or name != 'home-gate.json':
                return False
            spec = importlib.util.spec_from_file_location(
                'template_home_validation', Path(__file__).with_name('template-home-witness.py'))
            policy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(policy)
            return policy.load_home_gate(root, gate, identity, deadline_ns, now_ns)
        if scene in ('Notes written marker', 'Notes saved marker'):
            if (not expected_marker or gate.get('observed_marker') != expected_marker or
                    'continuity' not in gate):
                return False
        receipt, bitmap = read_capture(root, gate)
        if 'continuity' in gate:
            worker = json.loads((root / 'worker.json').read_text())
            if gate['worker_pid'] != worker['pid'] or gate['worker_start_ticks'] != worker['start_ticks']:
                return False
            current, current_bitmap = read_capture(root, gate['continuity'])
            before, before_bitmap = read_capture(root, gate['transition_from'])
            if (int(before['observed_monotonic_ns']) < identity['hello_monotonic_ns'] or
                    not valid_scene_transition(before, receipt, before_bitmap, bitmap)):
                return False
            return valid_continuity_gate(gate, receipt, current, bitmap, current_bitmap,
                                         identity, scene, deadline_ns, now_ns)
        return valid_gate(gate, receipt, identity, scene, deadline_ns, now_ns)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--saved-marker', action='store_true', help='read worker: inspected saved marker, never new editor')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'plan_only': True, 'home_deadline_seconds': 129,
                          'editor_deadline_seconds': 179, 'clock': 'first verified HELLO, never reset',
                          'signal': 'ordinary SIGINT through owned pidfd', 'automatic_scene_assertion': False,
                          'second_scene': 'saved-marker' if args.saved_marker else 'editor'}))
        return
    root = args.run.resolve()
    started = json.loads((root / 'started.json').read_text())
    pid = started['frontend']['pid']
    ticks = started['frontend']['start_ticks']
    proc = Path('/proc') / str(pid)
    if (proc.stat().st_uid != os.geteuid() or started['uid'] != os.geteuid() or
            proc.joinpath('stat').read_text().rpartition(')')[2].split()[19] != ticks):
        raise RuntimeError('frontend identity mismatch')
    command = json.loads((root / 'command.json').read_text())['argv']
    if proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1] != command or '--worker' in command:
        raise RuntimeError('frontend argv mismatch')
    fd = os.pidfd_open(pid)
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    hello = None
    second = 'saved-marker' if args.saved_marker else 'editor'
    gates = [SCENES['home'], SCENES[second]]
    expected_marker = json.loads((root / 'inputs.json').read_text())['marker'] if args.saved_marker else None
    try:
        while not poll.poll(0):
            # Only newline-terminated records can establish the one session epoch.
            rows = (root / 'session.log').read_bytes().split(b'\n')[:-1]
            for row in rows:
                if row.startswith(b'admission hello_verified_observed_monotonic_ns='):
                    observed = int(row.split(b'=', 1)[1].split()[0])
                    if hello is not None and observed != hello:
                        raise RuntimeError('HELLO epoch changed; refusing deadline restart')
                    hello = observed
            if hello is None:
                time.sleep(.05)
                continue  # Production frontend already enforces pre-HELLO900/30/5.
            identity = {'frontend_pid': pid, 'start_ticks': ticks, 'hello_monotonic_ns': hello}
            now = time.monotonic_ns()
            for name, scene, seconds in gates:
                deadline = hello + seconds * 1_000_000_000
                if now >= deadline and not load_gate(root, name, identity, scene, deadline, now, expected_marker):
                    if not poll.poll(0):
                        signal.pidfd_send_signal(fd, signal.SIGINT)
                    result = {'ordinary_stop_requested': True, 'missing_gate': name,
                              'deadline_monotonic_ns': deadline, 'observed_monotonic_ns': now,
                              'identity': identity, 'guest_acceptance': False}
                    (root / 'visual-gate-stop.json').write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result), flush=True)
                    return
            if now >= hello + 179_000_000_000:
                print(json.dumps({'both_scene_prerequisites_present': True, 'guest_acceptance': False}), flush=True)
                return
            time.sleep(.05)
    except BaseException as error:
        if not poll.poll(0):
            signal.pidfd_send_signal(fd, signal.SIGINT)
        (root / 'visual-gate-error.json').write_text(json.dumps({
            'ordinary_stop_requested': True, 'error': str(error), 'guest_acceptance': False,
            'frontend_pid': pid, 'start_ticks': ticks, 'observed_monotonic_ns': time.monotonic_ns()}) + '\n')
        raise
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
