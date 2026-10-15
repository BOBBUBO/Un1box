#!/usr/bin/env python3
"""MachineNone useful-block/cache/exit contracts; external guard required.

No firmware, real board, iOS, Android or throughput result. Original25s total
observation bound; each running phase keeps its original5s deadline.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import socket
import struct
import subprocess
import time

spec = importlib.util.spec_from_file_location('reference',
    pathlib.Path(__file__).with_name('arm1176-mmu-reference.py'))
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class GDB(reference.GDB):
    def start(self):
        self.s.sendall(b'$c#63')
        if self.byte() != b'+':
            raise RuntimeError('Continue packet not acknowledged')

    def stopped(self):
        if self.byte() != b'$':
            raise RuntimeError('Missing async stop packet')
        data = bytearray()
        while (byte := self.byte()) != b'#':
            data += byte
        checksum = self.byte() + self.byte()
        if int(checksum, 16) != sum(data) & 255:
            raise RuntimeError('Async stop checksum')
        self.s.sendall(b'+')
        reply = data.decode()
        if not reply.startswith(('S', 'T')):
            raise RuntimeError('Unexpected async stop: ' + reply)
        return reply


PATTERN = re.compile(r'^LT_DYNARMIC_BLOCK run=(\d+) count=(\d+) '
    r'pc=0x([0-9a-f]+) next=0x([0-9a-f]+) r0=(\d+) reused=(\d+) '
    r'code_reads=(\d+) data_reads=(\d+) jit=(\d+) generation=(\d+)$')


def witnesses(path):
    rows = []
    # Live reads can see the last fprintf before its terminating newline.
    # Only complete records may become evidence; the stopped final log is
    # checked separately for an incomplete final witness.
    for line in path.read_text(errors='replace').split('\n')[:-1]:
        if line.startswith('LT_DYNARMIC_BLOCK '):
            match = PATTERN.fullmatch(line)
            if not match:
                raise RuntimeError('Malformed block witness: ' + line)
            values = list(match.groups())
            row = dict(zip(['run', 'count', 'pc', 'next', 'r0', 'reused',
                            'code_reads', 'data_reads', 'jit', 'generation'],
                [int(v, 16 if i in [2, 3] else 10) for i, v in enumerate(values)]))
            rows.append(row)
    return rows


WARM_WINDOW = 8


def warm_pair_end(rows):
    """Require two consecutive genuine cache hits within first eight blocks."""
    for index in range(1, min(len(rows), WARM_WINDOW)):
        pair = rows[index-1:index+1]
        if all(row['reused'] and not row['code_reads'] for row in pair) and \
                pair[0]['generation'] == pair[1]['generation']:
            return index
    return None


def validate_async_phase(phase, baseline, before, rows, state):
    """Pure evidence checks; never executes or repairs the engine."""
    if not rows or rows[-1]['run'] >= 16384:
        raise RuntimeError('Missing blocks or diagnostic cap reached')
    warm_end = warm_pair_end(rows)
    if warm_end is None:
        raise RuntimeError('No two warm hits within first eight compiled blocks')
    increment = 2 if phase == 'modified-code-resume' else 1
    previous_generation = before[-1]['generation'] if before else 0
    prior_generation = previous_generation
    for index, row in enumerate(rows):
        if row['run'] != len(before)+index+1 or \
                row['r0'] != baseline['regs'][0]+increment*(index+1):
            raise RuntimeError('Architectural progress discontinuity')
        if row['count'] != 2 or row['pc'] != 0x1a00 or \
                row['next'] != 0x1a00 or row['data_reads'] or row['jit'] != 1:
            raise RuntimeError('Unexpected actual loop execution contract')
        if row['reused']:
            if row['code_reads'] or row['generation'] != prior_generation:
                raise RuntimeError('Cache hit has inconsistent generation/code reads')
        elif not row['code_reads'] or row['generation'] <= prior_generation:
            raise RuntimeError('Compilation has no fresh generation/code reads')
        prior_generation = row['generation']
    if phase in ['pause', 'modified-code-resume'] and rows[0]['reused']:
        raise RuntimeError('Initial or modified code reused stale compilation')
    expected = {'regs': list(baseline['regs']), 'cpsr': baseline['cpsr']}
    expected['regs'][0] += increment*len(rows)
    expected['regs'][15] = 0x1a00
    if state != expected or state['regs'][0] != rows[-1]['r0']:
        raise RuntimeError('Full stopped state differs from exact committed loop state')
    return {'baseline': baseline, 'increment': increment,
            'first_generation': rows[0]['generation'],
            'previous_generation': previous_generation,
            'resume_recompiled': phase == 'resume' and not rows[0]['reused'],
            'warm_pair_end_index': warm_end, 'warm_window_blocks': WARM_WINDOW,
            'exact_continuity': True, 'full_stopped_state_equal': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--suite', choices=['contracts', 'async'], required=True)
    parser.add_argument('--require-block', action='store_true')
    args = parser.parse_args()
    if args.suite == 'async' and not args.require_block:
        parser.error('async requires --require-block; use contracts for OFF control')
    root = args.output.resolve()
    root.mkdir(exist_ok=False)
    gpath, qpath = root/'g.sock', root/'q.sock'
    if len(str(gpath)) >= 100:
        raise RuntimeError('Use a shorter new private output path')
    argv = [str(args.qemu.resolve()), '-M', 'none', '-cpu',
        'arm1176,has_el3=off', '-m', '16M', '-accel',
        'tcg,thread=single,tb-size=8', '-nodefaults', '-display', 'none',
        '-serial', 'none', '-monitor', 'none', '-S', '-qmp',
        f'unix:{qpath},server=on,wait=off', '-gdb',
        f'unix:{gpath},server=on,wait=off']
    (root/'command.json').write_text(json.dumps(argv, indent=2)+'\n')
    started = time.monotonic()
    result = {'scope': __doc__, 'suite': args.suite, 'cases': [], 'argv': argv}
    process = stream = qsock = gdb = None
    running = False

    def check_time():
        if time.monotonic()-started > 25:
            raise TimeoutError('Original25s total observation deadline')

    def qmp(name):
        stream.write((json.dumps({'execute': name})+'\n').encode())
        while True:
            reply = json.loads(stream.readline())
            if 'event' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError(reply)
            return reply['return']

    def start():
        nonlocal running
        check_time()
        gdb.start()
        running = True

    def stop():
        nonlocal running
        began = time.monotonic()
        qmp('stop')
        reply = gdb.stopped()
        running = False
        if time.monotonic()-began > 2:
            raise TimeoutError('Ordinary stop acknowledgment exceeded2s')
        return reply

    def setup(pc, code, inputs=None):
        gdb.set(25, 0x10)
        for reg in range(15):
            gdb.set(reg, 0)
        gdb.write(pc, struct.pack('<'+'I'*len(code), *code))
        for reg, value in (inputs or {}).items():
            gdb.set(reg, value)
        gdb.set(15, pc)

    def reach_mode(mode):
        deadline = time.monotonic()+5
        start()
        while True:
            time.sleep(.01)
            reply = stop()
            state = gdb.state()
            if state['cpsr'] & 31 == mode:
                return state, reply
            if time.monotonic() >= deadline:
                raise TimeoutError('Original5s architectural-mode deadline')
            start()

    def record(name, state, expected, before_witnesses, expected_blocks):
        row = {'name': name, 'actual': state, 'expected': expected,
               'witnesses': witnesses(root/'engine.log')[before_witnesses:],
               'expected_blocks': expected_blocks}
        row['passed'] = all(state['cpsr'] & 31 == v if k == 'mode'
                           else state['regs'][int(k[1:])] == v
                           for k, v in expected.items())
        result['cases'].append(row)
        (root/'progress.json').write_text(json.dumps(result, indent=2)+'\n')
        if not row['passed']:
            raise RuntimeError('Unexpected actual architectural state: '+name)
        return row

    with (root/'engine.log').open('wb') as log:
        try:
            env = os.environ.copy()
            env.pop('LT_DYNARMIC_PROBE', None)
            env.pop('LT_DYNARMIC_BLOCK_PROBE', None)
            if args.require_block:
                env['LT_DYNARMIC_BLOCK_PROBE'] = '1'
            process = subprocess.Popen(argv, stdout=log, stderr=log, env=env)
            result['pid'] = process.pid
            while not gpath.exists() or not qpath.exists():
                if process.poll() is not None or time.monotonic()-started > 5:
                    raise RuntimeError('Original5s socket startup deadline/exit')
                time.sleep(.02)
            qsock = socket.socket(socket.AF_UNIX)
            qsock.settimeout(2)
            qsock.connect(str(qpath))
            stream = qsock.makefile('rwb', buffering=0)
            if 'QMP' not in json.loads(stream.readline()):
                raise RuntimeError('Missing QMP greeting')
            qmp('qmp_capabilities')
            gdb = GDB(gpath)
            gdb.packet('qSupported:qXfer:features:read+')
            core = gdb.packet('qXfer:features:read:arm-core.xml:0,fff')
            (root/'arm-core.xml.response').write_text(core)
            if 'name="cpsr"' not in core or 'regnum="25"' not in core:
                raise RuntimeError('Unexpected core register schema')
            gdb.write(0, struct.pack('<8I', *([0xeafffffe]*8)))
            table = bytearray(16384)
            struct.pack_into('<3I', table, 0, 0xc02, 0x402, 0xc12)
            for offset in range(0, len(table), 256):
                gdb.write(0x4000+offset, table[offset:offset+256])
            boot = [0xe59f00f8, 0xee020f10, 0xe3a00001, 0xee030f10,
                    0xe59f00ec, 0xee010f10, 0xeafffffe]
            gdb.write(0x1000, struct.pack('<7I', *boot))
            gdb.write(0x1100, struct.pack('<2I', 0x4000, 0x0085007b))
            gdb.write(0x2000, struct.pack('<3I', 42, 99, 123))
            gdb.set(25, 0xd3)
            gdb.set(15, 0x1000)
            for _ in range(6):
                gdb.step()
            result['bootstrap'] = gdb.state()
            if result['bootstrap']['regs'][15] != 0x1018:
                raise RuntimeError('Real CP15 bootstrap failed')
            if args.suite == 'contracts':
                for increment, limit, name in [(1,3,'reusable-dynamic-load-loop'),
                                               (2,6,'changed-code-loop')]:
                    before = len(witnesses(root/'engine.log'))
                    code = [0xe5910000, 0xe2811004, 0xe2822000|increment,
                            0xe3520000|limit, 0x1afffffa, 0xef000001]
                    setup(0x1500, code, {1:0x2000})
                    state, _ = reach_mode(0x13)
                    record(name, state, {'r0':123,'r1':0x200c,'r2':limit,
                           'r14':0x1518,'r15':8,'mode':0x13}, before, 3)
                for target, opcode, name, expected in [
                    (0x102000,0xeaffffff,'precise-data-fault-after-block',
                     {'r0':42,'r2':1,'r3':19,'r14':0x1614,'r15':0x10,'mode':0x17}),
                    (0x2000,0xea0ffffc,'precise-fetch-fault-after-block',
                     {'r0':42,'r2':1,'r3':19,'r14':0x401604,'r15':0xc,'mode':0x17})]:
                    before = len(witnesses(root/'engine.log'))
                    setup(0x1600,[0xe5910000,0xe2822001,opcode,0xe5943000],
                          {1:0x2000,3:19,4:target})
                    state,_ = reach_mode(0x17)
                    record(name,state,expected,before,1)
                for pc,code,name,value,lr in [
                    (0x1700,[0xe2800001,0xef000001,0xeafffffe],
                     'unsupported-tail-tcg',1,0x1708),
                    (0x1800,[0xe2800001]*8+[0xef000001],
                     'no-branch-budget-tcg',8,0x1824)]:
                    before=len(witnesses(root/'engine.log'))
                    setup(pc,code)
                    state,_=reach_mode(0x13)
                    record(name,state,{'r0':value,'r14':lr,'r15':8,'mode':0x13},before,0)
                before=len(witnesses(root/'engine.log'))
                setup(0x1500,[0xe5910000,0xe2811004,0xe2822001,
                              0xe3520003,0x1afffffa,0xef000001],{1:0x2000})
                gdb.step()
                record('single-step-tcg',gdb.state(),
                       {'r0':42,'r15':0x1504,'mode':0x10},before,0)
                for packet,remove,name in [('Z0,1500,4','z0,1500,4','breakpoint-tcg'),
                                          ('Z3,2000,4','z3,2000,4','watchpoint-tcg')]:
                    before=len(witnesses(root/'engine.log'))
                    setup(0x1500,[0xe5910000,0xe2811004,0xe2822001,
                                  0xe3520003,0x1afffffa,0xef000001],{1:0x2000})
                    if gdb.packet(packet)!='OK':
                        raise RuntimeError('Debugger instrumentation unsupported')
                    reply=gdb.packet('c')
                    if not reply.startswith(('S05','T05')):
                        raise RuntimeError('Expected real debug trap')
                    state=gdb.state()
                    if gdb.packet(remove)!='OK':
                        raise RuntimeError('Debugger cleanup failed')
                    row=record(name,state,{'mode':0x10},before,0)
                    row['debug_reply']=reply
            else:
                setup(0x1a00,[0xe2800001,0xeafffffd])
                for phase in ['pause','resume','modified-code-resume']:
                    before_rows=witnesses(root/'engine.log')
                    if phase=='modified-code-resume':
                        gdb.write(0x1a00,struct.pack('<I',0xe2800002))
                    baseline=gdb.state()
                    deadline=time.monotonic()+5
                    start()
                    while True:
                        observed=witnesses(root/'engine.log')[len(before_rows):]
                        if warm_pair_end(observed) is not None:
                            break
                        if len(observed) >= WARM_WINDOW:
                            raise RuntimeError('No two warm hits within first eight compiled blocks')
                        check_time()
                        if time.monotonic()>=deadline:
                            raise TimeoutError('No bounded warm reuse before original5s deadline')
                        time.sleep(.001)
                    reply=stop()
                    state=gdb.state()
                    after=witnesses(root/'engine.log')[len(before_rows):]
                    last=after[-1]
                    if last['run']>=16384:
                        raise RuntimeError('Block cap reached; TCG cannot qualify async boundary')
                    row=record(phase,state,{'r0':last['r0'],'r15':0x1a00,
                               'mode':0x10},len(before_rows),None)
                    row['debug_reply']=reply
                    # A pending stop can conservatively hand off to TCG and
                    # invalidate code. Resumption may compile once again;
                    # require exact architectural continuity and bounded warm
                    # reuse instead of weakening that correct invalidation.
                    row['async_contract']=validate_async_phase(
                        phase,baseline,before_rows,after,state)
            result['status']='BLOCK_CONTRACTS_OBSERVED'
        except Exception as error:
            result['status']='FAILED'
            result['error']=str(error)
        finally:
            if process and process.poll() is None and stream:
                try:
                    if running and gdb:
                        stop()
                    qmp('quit')
                    process.wait(timeout=3)
                except Exception as error:
                    result['status']='FAILED'
                    result['quit_error']=str(error)
            result['exit_code']=process.poll() if process else None
            if process and process.poll() is None:
                result['status']='FAILED'
            actual=witnesses(root/'engine.log')
            log_text=(root/'engine.log').read_text(errors='replace')
            if log_text and not log_text.endswith('\n'):
                result['status']='FAILED'
                result['error']='Incomplete final engine log record'
            result['actual_block_witnesses']=actual
            valid=True
            if args.suite=='contracts':
                valid &= len(result['cases']) == 9
                for row in result['cases']:
                    expected=row['expected_blocks'] if args.require_block else 0
                    valid &= len(row['witnesses'])==expected
                    if args.require_block and row['expected_blocks']==3 and len(row['witnesses'])==3:
                        ws=row['witnesses']
                        valid &= [w['r0'] for w in ws]==[42,99,123]
                        valid &= all(w['count']==5 and w['data_reads']==1 and w['jit']==1 for w in ws)
                        valid &= bool(ws[0]['code_reads']) and not ws[0]['reused']
                        valid &= all(w['reused'] and not w['code_reads'] for w in ws[1:])
            if args.suite == 'async':
                valid &= len(result['cases']) == 3
            if not args.require_block:
                valid &= not actual
            if not valid:
                result['status']='FAILED'
                result['witness_error']='Required actual compiled-block/cache witnesses differ'
            result['duration_seconds']=time.monotonic()-started
            (root/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='BLOCK_CONTRACTS_OBSERVED' and result['exit_code']==0 else 1


if __name__=='__main__':
    raise SystemExit(main())
