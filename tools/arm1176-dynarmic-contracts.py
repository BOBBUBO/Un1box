#!/usr/bin/env python3
"""UNRUN synthetic ARM1176 RAM/ALU/flags/branch differential input.

Requires an external owned-process resource/deadline guard. No firmware, real
board, graphics, guest, Android or performance qualification. Each invocation
uses a fresh machine-none process and at most eight eligible JIT steps.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import socket
import struct
import subprocess
import time


reference_path = pathlib.Path(__file__).with_name('arm1176-mmu-reference.py')
spec = importlib.util.spec_from_file_location('arm1176_reference', reference_path)
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


def cases(suite):
    """Explicit architecture vectors, not the engine's instruction decoder."""
    if suite == 'integer':
        return [
            ('mov-r3', 0xe3a0302a, {}, {'r3': 42}, True),
            ('add-r4', 0xe2834001, {3: 42}, {'r4': 43}, True),
            ('sub-r5', 0xe2445002, {4: 43}, {'r5': 41}, True),
            ('and-r6', 0xe205600f, {5: 41}, {'r6': 9}, True),
            ('eor-r7', 0xe22670ff, {6: 9}, {'r7': 246}, True),
            ('orr-r8', 0xe3878080, {7: 246}, {'r8': 246}, True),
            ('cmp-negative', 0xe35800ff, {8: 246},
             {'cpsr': 0x80000010}, True),
            ('add-wrap-no-flags', 0xe2834001, {3: 0xffffffff},
             {'r4': 0}, True),
        ]
    if suite == 'flags-branch':
        return [
            ('cmp-borrow', 0xe3500001, {0: 0},
             {'cpsr': 0x80000010}, True),
            ('bne-taken', 0x1a000000, {}, {'r15': 0x1408}, True),
            ('beq-not-taken', 0x0a000000, {}, {'r15': 0x1404}, True),
            ('cmp-equal', 0xe3500000, {}, {'cpsr': 0x60000010}, True),
            ('beq-taken', 0x0a000000, {25: 0x60000010},
             {'r15': 0x1408, 'cpsr': 0x60000010}, True),
            ('bne-not-taken', 0x1a000000, {25: 0x60000010},
             {'r15': 0x1404, 'cpsr': 0x60000010}, True),
            ('cmp-overflow', 0xe3500001, {0: 0x80000000},
             {'cpsr': 0x30000010}, True),
            ('branch-negative-displacement', 0xeafffffd, {},
             {'r15': 0x13fc}, True),
        ]
    if suite == 'memory':
        fault = {'r0': 19, 'r2': 0, 'r14': 0x1408,
                 'r15': 0x10, 'mode': 0x17}
        return [
            ('ldr-readable', 0xe5910000, {1: 0x2000}, {'r0': 42}, True),
            ('ldr-offset-r3', 0xe5913004, {1: 0x2000},
             {'r3': 0x12345678}, True),
            ('ldr-base-destination-alias', 0xe5911000, {1: 0x2000},
             {'r1': 42}, True),
            ('ldr-permission-abort', 0xe5910000,
             {0: 19, 1: 0x102000}, fault, False),
            ('ldr-translation-abort', 0xe5910000,
             {0: 19, 1: 0x302000}, fault, False),
            ('ldr-alignment-abort', 0xe5910000,
             {0: 19, 1: 0x2001}, fault, False),
            ('ldrb-tcg-fallback', 0xe5d10000, {1: 0x2000},
             {'r0': 42}, False),
            ('ldr-writeback-tcg-fallback', 0xe5b10004, {1: 0x2000},
             {'r0': 0x12345678, 'r1': 0x2004}, False),
            ('ldr-sp-tcg-fallback', 0xe591d000, {1: 0x2000},
             {'r13': 42}, False),
            ('add-sp-tcg-fallback', 0xe28d0001, {13: 10},
             {'r0': 11}, False),
            ('thumb-tcg-fallback', 0x0000202a, {25: 0x30},
             {'r0': 42, 'r15': 0x1402, 'cpsr': 0x30}, False),
        ]
    raise ValueError(suite)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--suite', choices=['integer', 'flags-branch', 'memory'],
                        required=True)
    parser.add_argument('--require-dynarmic', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(exist_ok=False)
    qmp_path, gdb_path = root / 'q.sock', root / 'g.sock'
    if len(str(gdb_path)) >= 100:
        raise RuntimeError('Use an isolated output path shorter than 90 characters')
    argv = [str(args.qemu.resolve()), '-M', 'none', '-cpu',
            'arm1176,has_el3=off', '-m', '16M', '-accel',
            'tcg,thread=single,tb-size=8', '-nodefaults', '-display', 'none',
            '-serial', 'none', '-monitor', 'none', '-S', '-qmp',
            f'unix:{qmp_path},server=on,wait=off', '-gdb',
            f'unix:{gdb_path},server=on,wait=off']
    (root / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
    started = time.monotonic()
    result = {'scope': __doc__, 'suite': args.suite, 'cases': [], 'argv': argv}
    process = stream = qmp_socket = None
    expected_witnesses = []

    def qmp(name):
        stream.write((json.dumps({'execute': name}) + '\n').encode())
        while True:
            reply = json.loads(stream.readline())
            if 'event' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError(reply)
            return reply['return']

    with (root / 'engine.log').open('wb') as log:
        try:
            environment = os.environ.copy()
            environment.pop('LT_DYNARMIC_PROBE', None)
            if args.require_dynarmic:
                environment['LT_DYNARMIC_PROBE'] = '1'
            process = subprocess.Popen(argv, stdout=log, stderr=log, env=environment)
            result['pid'] = process.pid
            while not qmp_path.exists() or not gdb_path.exists():
                if process.poll() is not None:
                    raise RuntimeError('QEMU exited during startup')
                if time.monotonic() - started > 5:
                    raise TimeoutError('Socket startup deadline')
                time.sleep(.02)
            qmp_socket = socket.socket(socket.AF_UNIX)
            qmp_socket.settimeout(2)
            qmp_socket.connect(str(qmp_path))
            stream = qmp_socket.makefile('rwb', buffering=0)
            if 'QMP' not in json.loads(stream.readline()):
                raise RuntimeError('Missing QMP greeting')
            qmp('qmp_capabilities')
            gdb = reference.GDB(gdb_path)
            gdb.packet('qSupported:qXfer:features:read+')
            core_xml = gdb.packet('qXfer:features:read:arm-core.xml:0,fff')
            (root / 'arm-core.xml.response').write_text(core_xml)
            if 'name="cpsr"' not in core_xml or 'regnum="25"' not in core_xml:
                raise RuntimeError('Unexpected ARM core register schema')
            gdb.write(0, struct.pack('<8I', *([0xeafffffe] * 8)))
            table = bytearray(16384)
            struct.pack_into('<3I', table, 0, 0x00000c02, 0x00000402, 0x00000c12)
            for offset in range(0, len(table), 256):
                gdb.write(0x4000 + offset, table[offset:offset + 256])
            boot = [0xe59f00f8, 0xee020f10, 0xe3a00001, 0xee030f10,
                    0xe59f00ec, 0xee010f10, 0xeafffffe]
            gdb.write(0x1000, struct.pack('<7I', *boot))
            # Prospective alignment-negative uses the actual SCTLR.A bit.
            sctlr = 0x0085007b if args.suite == 'memory' else 0x00850079
            gdb.write(0x1100, struct.pack('<2I', 0x4000, sctlr))
            gdb.write(0x2000, struct.pack('<2I', 42, 0x12345678))
            gdb.set(25, 0xd3)
            gdb.set(15, 0x1000)
            for _ in range(6):
                gdb.step()
            result['bootstrap'] = gdb.state()
            if result['bootstrap']['regs'][15] != 0x1018 or \
                    result['bootstrap']['cpsr'] & 31 != 0x13:
                raise RuntimeError('Actual CP15 bootstrap failed')

            for name, opcode, inputs, expectations, eligible in cases(args.suite):
                if time.monotonic() - started > 25:
                    raise TimeoutError('Original observation deadline')
                gdb.set(25, 0x10)
                for reg in range(15):
                    gdb.set(reg, 0)
                gdb.write(0x1400, struct.pack('<2I', opcode, 0xe3a02077))
                for reg, value in inputs.items():
                    gdb.set(reg, value)
                gdb.set(15, 0x1400)
                before = gdb.state()
                expected = {'r15': 0x1404, 'mode': 0x10}
                if 'mode' not in expectations or expectations['mode'] == 0x10:
                    expected['cpsr'] = before['cpsr']
                expected.update(expectations)
                gdb.step()
                state = gdb.state()
                row = {'name': name, 'opcode': opcode, 'before': before,
                       'actual': state, 'expected': expected,
                       'alternate_eligible': eligible}
                def matches(key, value):
                    if key == 'mode':
                        return state['cpsr'] & 31 == value
                    if key == 'cpsr':
                        return state['cpsr'] == value
                    return state['regs'][int(key[1:])] == value
                row['passed'] = all(matches(k, v) for k, v in expected.items())
                result['cases'].append(row)
                (root / 'progress.json').write_text(json.dumps(result, indent=2) + '\n')
                if not row['passed']:
                    raise RuntimeError('Unexpected real ARM1176 state: ' + name)
                if eligible:
                    expected_witnesses.append(
                        f'LT_DYNARMIC_PROBE executed={len(expected_witnesses)+1} '
                        f'pc=0x00001400 opcode=0x{opcode:08x} r0={state["regs"][0]}')
            result['status'] = 'CONTRACTS_OBSERVED'
        except Exception as error:
            result['status'] = 'FAILED'
            result['error'] = str(error)
        finally:
            if process and process.poll() is None and stream:
                try:
                    qmp('quit')
                    process.wait(timeout=3)
                except Exception as error:
                    result['quit_error'] = str(error)
            if process:
                result['exit_code'] = process.poll()
                if process.poll() is None:
                    result['status'] = 'FAILED'
            actual_witnesses = [line for line in
                (root / 'engine.log').read_text(errors='replace').splitlines()
                if line.startswith('LT_DYNARMIC_PROBE executed=')]
            result['actual_witnesses'] = actual_witnesses
            result['expected_witnesses'] = expected_witnesses if args.require_dynarmic else []
            if actual_witnesses != result['expected_witnesses']:
                result['status'] = 'FAILED'
                result['witness_error'] = 'Exact actual execution witnesses differ'
            result['duration_seconds'] = time.monotonic() - started
            (root / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
            if stream:
                stream.close()
            if qmp_socket:
                qmp_socket.close()
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'CONTRACTS_OBSERVED' and result['exit_code'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
