#!/usr/bin/env python3
"""Asset-free real ARM1176 QEMU reference for a possible hybrid CPU adapter.

Uses an existing executable, machine=none, 16MiB RAM and legal hand-written
instructions. No iOS, guest assets, graphics, Android or speed qualification.
The caller must provide an aggregate resource/deadline guard for the process
tree. This script has its own fixed 25-second observation bound and QMP quit.
"""
import argparse
import json
import pathlib
import socket
import struct
import subprocess
import time


class GDB:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(2)
        self.s.connect(str(path))

    def byte(self):
        b = self.s.recv(1)
        if not b:
            raise RuntimeError('GDB disconnected')
        return b

    def packet(self, command):
        body = command.encode()
        self.s.sendall(b'$' + body + b'#' + f'{sum(body) & 255:02x}'.encode())
        while True:
            b = self.byte()
            if b == b'$':
                break
            if b == b'-':
                raise RuntimeError('GDB rejected packet')
        answer = bytearray()
        while (b := self.byte()) != b'#':
            answer += b
        checksum = self.byte() + self.byte()
        if int(checksum, 16) != sum(answer) & 255:
            raise RuntimeError('GDB checksum mismatch')
        self.s.sendall(b'+')
        text = answer.decode()
        if text.startswith('E'):
            raise RuntimeError('GDB error: ' + text)
        return text

    def set(self, reg, value):
        answer = self.packet(f'P{reg:x}=' + struct.pack('<I', value).hex())
        if answer != 'OK':
            raise RuntimeError(f'GDB register write failed: {answer}')

    def get(self, reg):
        data = bytes.fromhex(self.packet(f'p{reg:x}'))
        if len(data) != 4:
            raise RuntimeError('Unexpected register width')
        return int.from_bytes(data, 'little')

    def write(self, address, data):
        answer = self.packet(f'M{address:x},{len(data):x}:' + data.hex())
        if answer != 'OK':
            raise RuntimeError('GDB memory write failed: ' + answer)

    def step(self):
        answer = self.packet('s')
        if not answer.startswith(('S05', 'T05')):
            raise RuntimeError('Unexpected step stop: ' + answer)

    def state(self):
        return {'regs': [self.get(i) for i in range(16)], 'cpsr': self.get(25)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('qemu', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(exist_ok=False)
    # Short Unix socket paths avoid host sun_path limits.
    qmp_path, gdb_path = root / 'q.sock', root / 'g.sock'
    if len(str(gdb_path)) >= 100:
        raise RuntimeError('Use an isolated output path shorter than 90 characters')
    argv = [str(args.qemu.resolve()), '-M', 'none', '-cpu',
            'arm1176,has_el3=off', '-m', '16M', '-accel', 'tcg,tb-size=8',
            '-nodefaults', '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-S', '-qmp', f'unix:{qmp_path},server=on,wait=off',
            '-gdb', f'unix:{gdb_path},server=on,wait=off']
    (root / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
    started = time.monotonic()
    result = {'scope': __doc__, 'cases': [], 'argv': argv}
    process = None
    stream = None
    qmp_socket = None

    def command(name):
        stream.write((json.dumps({'execute': name}) + '\n').encode())
        while True:
            reply = json.loads(stream.readline())
            if 'event' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError(reply)
            return reply['return']

    def record(name, state, expected):
        row = {'name': name, 'actual': state, 'expected': expected}
        row['passed'] = all(state['regs'][int(k[1:])] == v if k.startswith('r')
                            else (state['cpsr'] & 31) == v for k, v in expected.items())
        result['cases'].append(row)
        (root / 'progress.json').write_text(json.dumps(result, indent=2) + '\n')
        if not row['passed']:
            raise RuntimeError('Unexpected real ARM1176 state: ' + name)

    with (root / 'engine.log').open('wb') as log:
        try:
            process = subprocess.Popen(argv, stdout=log, stderr=log)
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
            command('qmp_capabilities')
            gdb = GDB(gdb_path)
            gdb.packet('qSupported:qXfer:features:read+')
            target_xml = gdb.packet('qXfer:features:read:target.xml:0,fff')
            (root / 'target.xml.response').write_text(target_xml)
            # Preserve the independently inspectable ARM core register schema.
            core_xml = gdb.packet('qXfer:features:read:arm-core.xml:0,fff')
            (root / 'arm-core.xml.response').write_text(core_xml)
            if 'name="cpsr"' not in core_xml or 'regnum="25"' not in core_xml:
                raise RuntimeError('Unexpected ARM core register schema')
            # Install all vectors as B . (no firmware or device initialization).
            gdb.write(0, struct.pack('<8I', *([0xeafffffe] * 8)))
            # First section: full user/kernel RW identity. Second: privileged
            # RW only mapping to physical section0. Third: full RW but XN.
            table = bytearray(16384)
            struct.pack_into('<3I', table, 0, 0x00000c02, 0x00000402, 0x00000c12)
            for offset in range(0, len(table), 256):
                gdb.write(0x4000 + offset, table[offset:offset+256])
            boot = [0xe59f00f8, 0xee020f10, 0xe3a00001, 0xee030f10,
                    0xe59f00ec, 0xee010f10, 0xeafffffe]
            gdb.write(0x1000, struct.pack('<7I', *boot))
            gdb.write(0x1100, struct.pack('<2I', 0x4000, 0x00850079))
            gdb.write(0x2000, struct.pack('<I', 42))
            gdb.set(25, 0xd3)  # SVC, IRQ/FIQ disabled.
            gdb.set(15, 0x1000)
            for _ in range(6):
                gdb.step()
            record('mmu-enabled-bootstrap', gdb.state(), {'r15': 0x1018, 'mode': 0x13})
            # Reusable LDR with sentinel destination and following instruction.
            gdb.write(0x1200, struct.pack('<3I', 0xe5910000, 0xe3a02077, 0xeafffffe))
            for name, address, expected_pc, expected_mode in [
                ('el0-readable', 0x2000, 0x1204, 0x10),
                ('el0-permission-abort', 0x102000, 0x10, 0x17),
                ('el0-translation-abort', 0x302000, 0x10, 0x17),
            ]:
                if time.monotonic() - started > 25:
                    raise TimeoutError('Reference observation deadline')
                gdb.set(25, 0x10)
                gdb.set(0, 19)
                gdb.set(1, address)
                gdb.set(2, 0)
                gdb.set(15, 0x1200)
                gdb.step()
                expected = {'r0': 42 if name == 'el0-readable' else 19,
                            'r2': 0, 'r15': expected_pc, 'mode': expected_mode}
                if name != 'el0-readable':
                    expected['r14'] = 0x1208
                record(name, gdb.state(), expected)
            # The current engine's get_S1prot clears XN before ARMv7. Keep
            # this observed implementation boundary explicit, rather than
            # claiming that ARM1176 execute-never protection was qualified.
            gdb.set(25, 0x10)
            gdb.set(0, 19)
            gdb.set(1, 0x2000)
            gdb.set(15, 0x201200)
            gdb.step()
            record('reference-pre-v7-xn-ignored', gdb.state(),
                   {'r0': 42, 'r2': 0, 'r15': 0x201204, 'mode': 0x10})
            # A genuinely unmapped instruction fetch must enter prefetch abort.
            gdb.set(25, 0x10)
            gdb.set(15, 0x401200)
            gdb.step()
            record('el0-unmapped-prefetch-abort', gdb.state(),
                   {'r15': 0x0c, 'r14': 0x401204, 'mode': 0x17})
            result['status'] = 'REFERENCE_OBSERVED'
        except Exception as error:
            result['status'] = 'FAILED'
            result['error'] = str(error)
        finally:
            if process and process.poll() is None and stream:
                try:
                    command('quit')
                    process.wait(timeout=3)
                except Exception as error:
                    result['quit_error'] = str(error)
            if process:
                result['exit_code'] = process.poll()
            # The external owner guard retires any remaining owned process;
            # absence of a normal exit is always a failed result here.
            if process and process.poll() is None:
                result['status'] = 'FAILED'
            result['duration_seconds'] = time.monotonic() - started
            (root / 'RESULT.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'REFERENCE_OBSERVED' and result['exit_code'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
