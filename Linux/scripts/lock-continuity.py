#!/usr/bin/env python3
"""Exact stable anchors for an ACTUALLY inspected n72 Lock Screen.

This compares declared regions, never recognizes or labels a new scene. Full
original/current native BMPs remain evidence. Clock digits, animated caption
and other pixels outside these anchors are explicitly not compared.
"""
import hashlib
import struct

POLICY = 'n72-lock-earth-knob-symbol-v1'
SHAPE = (320, 480)
# Coordinates in the native portrait320x480 image, end coordinates exclusive.
# Earth includes its complete outline. The slider knob and lock symbol remain
# protected; the clock and animated text to the right of the knob do not.
REGIONS = (('earth', (28, 120, 292, 399)),
           ('slider_knob', (20, 407, 94, 459)),
           ('lock_symbol', (150, 1, 170, 18)))
EXCLUDED_DYNAMIC = (('clock_digits', (44, 24, 277, 90)),
                    ('slider_caption_shimmer', (94, 410, 298, 458)))


def bitmap(data):
    if len(data) != 54 + 320 * 480 * 4 or data[:2] != b'BM':
        raise ValueError('expected bounded native n72 BMP')
    size, = struct.unpack_from('<I', data, 2)
    offset, = struct.unpack_from('<I', data, 10)
    dib, width, height, planes, bits, compression = struct.unpack_from('<IiiHHI', data, 14)
    if (size != len(data) or offset != 54 or dib != 40 or (width, abs(height)) != SHAPE or
            planes != 1 or bits != 32 or compression != 0):
        raise ValueError('unsupported BMP shape/format')
    return height


def region_bytes(data, height, box):
    x1, y1, x2, y2 = box
    return b''.join(data[54 + ((y if height < 0 else 479 - y) * 320 + x1) * 4:
                         54 + ((y if height < 0 else 479 - y) * 320 + x2) * 4]
                    for y in range(y1, y2))


def compare(original, current):
    """Return exact anchor results; unsupported shape rejects, no tolerances."""
    try:
        old_height, new_height = bitmap(original), bitmap(current)
    except (ValueError, struct.error) as error:
        return {'policy': POLICY, 'matched': False, 'error': str(error),
                'guest_acceptance': False, 'no_new_scene_asserted': True}
    results = []
    for name, box in REGIONS:
        old = region_bytes(original, old_height, box)
        new = region_bytes(current, new_height, box)
        results.append({'name': name, 'box': box, 'exact_match': old == new,
                        'original_sha256': hashlib.sha256(old).hexdigest(),
                        'current_sha256': hashlib.sha256(new).hexdigest()})
    return {'policy': POLICY, 'matched': all(x['exact_match'] for x in results),
            'shape': SHAPE, 'regions': results, 'excluded_dynamic': EXCLUDED_DYNAMIC,
            'comparison_scope': 'only declared anchors; all other full-image pixels retained, not compared',
            'full_BMP_identical': original == current, 'no_new_scene_asserted': True,
            'guest_acceptance': False}
