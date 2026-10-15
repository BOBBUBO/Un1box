#!/usr/bin/env python3
"""UNRUN finite MachineNone throughput candidate; external resource guard required.

No iOS/Android/ARM64 or production speed claim. Original25s total observation
bound and original10s workload deadline. One real process, one finite workload.
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

spec = importlib.util.spec_from_file_location('blocks',
    pathlib.Path(__file__).with_name('arm1176-dynarmic-blocks.py'))
blocks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(blocks)

SUMMARY = re.compile(r'^LT_DYNARMIC_THROUGHPUT blocks=(\d+) instructions=(\d+) '
    r'cold=(\d+) warm=(\d+) first_us=(\d+) cold_end_us=(\d+) last_us=(\d+) '
    r'pc=0x([0-9a-f]+) r0=(\d+) r1=(\d+) cpsr=0x([0-9a-f]+)$')


def process_stat(path):
    raw = path.read_text()
    fields = raw[raw.rfind(')')+2:].split()
    return {'raw': raw, 'state': fields[0], 'utime_ticks': int(fields[11]),
            'stime_ticks': int(fields[12]), 'starttime_ticks': int(fields[19])}


def summaries(path):
    records = []
    for line in path.read_text(errors='replace').splitlines():
        if line.startswith('LT_DYNARMIC_THROUGHPUT '):
            match = SUMMARY.fullmatch(line)
            if not match:
                raise RuntimeError('Malformed aggregate witness')
            keys = ['blocks','instructions','cold','warm','first_us',
                    'cold_end_us','last_us','pc','r0','r1','cpsr']
            records.append(dict(zip(keys,[int(v,16 if i in [7,10] else 10)
                for i,v in enumerate(match.groups())])))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--iterations', type=int, choices=[1,65536], required=True)
    parser.add_argument('--require-throughput', action='store_true')
    args = parser.parse_args()
    root=args.output.resolve()
    root.mkdir(exist_ok=False)
    gpath,qpath=root/'g.sock',root/'q.sock'
    if len(str(gpath))>=100:
        raise RuntimeError('Use a shorter fresh private output path')
    argv=[str(args.qemu.resolve()),'-M','none','-cpu','arm1176,has_el3=off',
          '-m','16M','-accel','tcg,thread=single,tb-size=8','-nodefaults',
          '-display','none','-serial','none','-monitor','none','-S','-qmp',
          f'unix:{qpath},server=on,wait=off','-gdb',f'unix:{gpath},server=on,wait=off']
    (root/'command.json').write_text(json.dumps(argv,indent=2)+'\n')
    result={'scope':__doc__,'iterations':args.iterations,'argv':argv,'status':'FAILED',
            'ticks_per_second':os.sysconf('SC_CLK_TCK'),
            'timing_limitations':'CPU ticks include QMP/loop/preparation/commit; short cold ticks may be zero. Wall completion is bounded by1ms polling and at least5ms wait, not guest latency. WFI sleep observation is an indicator; full final state must confirm completion.'}
    process=stream=qsock=gdb=None
    running=False
    started=time.monotonic()

    def qmp(name,arguments=None):
        request={'execute':name}
        if arguments is not None:
            request['arguments']=arguments
        stream.write((json.dumps(request)+'\n').encode())
        while True:
            raw=stream.readline()
            if not raw:
                raise RuntimeError('QMP disconnected')
            reply=json.loads(raw)
            if 'event' in reply:
                continue
            if 'error' in reply:
                raise RuntimeError(reply)
            return reply['return']

    def stop():
        nonlocal running
        qmp('stop')
        reply=gdb.stopped()
        running=False
        return reply

    with (root/'engine.log').open('wb') as log:
        try:
            env=os.environ.copy()
            for switch in ['LT_DYNARMIC_PROBE','LT_DYNARMIC_BLOCK_PROBE','LT_DYNARMIC_THROUGHPUT']:
                env.pop(switch,None)
            if args.require_throughput:
                env.update(LT_DYNARMIC_BLOCK_PROBE='1',LT_DYNARMIC_THROUGHPUT='1')
            result['experiment_environment']={k:v for k,v in env.items() if k.startswith('LT_DYNARMIC')}
            process=subprocess.Popen(argv,stdout=log,stderr=log,env=env)
            result['pid']=process.pid
            while not gpath.exists() or not qpath.exists():
                if process.poll() is not None or time.monotonic()-started>5:
                    raise RuntimeError('Original5s startup deadline or early exit')
                time.sleep(.02)
            qsock=socket.socket(socket.AF_UNIX)
            qsock.settimeout(2)
            qsock.connect(str(qpath))
            stream=qsock.makefile('rwb',buffering=0)
            if 'QMP' not in json.loads(stream.readline()):
                raise RuntimeError('Missing QMP greeting')
            qmp('qmp_capabilities')
            gdb=blocks.GDB(gpath)
            gdb.packet('qSupported:qXfer:features:read+')
            core=gdb.packet('qXfer:features:read:arm-core.xml:0,fff')
            (root/'arm-core.xml.response').write_text(core)
            if 'name="cpsr"' not in core or 'regnum="25"' not in core:
                raise RuntimeError('Unexpected ARM core schema')
            # Actual short-descriptor MMU bootstrap, as in prior contracts.
            gdb.write(0,struct.pack('<8I',*([0xeafffffe]*8)))
            table=bytearray(16384)
            struct.pack_into('<3I',table,0,0xc02,0x402,0xc12)
            for offset in range(0,len(table),256):
                gdb.write(0x4000+offset,table[offset:offset+256])
            boot=[0xe59f00f8,0xee020f10,0xe3a00001,0xee030f10,
                  0xe59f00ec,0xee010f10,0xeafffffe]
            gdb.write(0x1000,struct.pack('<7I',*boot))
            gdb.write(0x1100,struct.pack('<2I',0x4000,0x0085007b))
            gdb.set(25,0xd3)
            gdb.set(15,0x1000)
            for _ in range(6):
                gdb.step()
            result['bootstrap']=gdb.state()
            if result['bootstrap']['regs'][15]!=0x1018:
                raise RuntimeError('Actual CP15 bootstrap failed')
            # SVC vector executes privileged ARMv6 CP15 WFI then B . .
            # No board device/timer exists to wake the CPU.
            gdb.write(8,struct.pack('<2I',0xee070f90,0xeafffffe))
            code=[0xe2800001,0xe2411001,0xe3510000,0x1afffffb,0xef000001]
            gdb.write(0x1c00,struct.pack('<5I',*code))
            gdb.set(25,0x10)
            for reg in range(15):
                gdb.set(reg,0)
            gdb.set(1,args.iterations)
            gdb.set(15,0x1c00)
            result['initial_state']=gdb.state()
            cpus=qmp('query-cpus-fast')
            if len(cpus)!=1:
                raise RuntimeError('Not a single-vCPU machine')
            tid=cpus[0]['thread-id']
            statpath=pathlib.Path(f'/proc/{process.pid}/stat')
            threadpath=pathlib.Path(f'/proc/{process.pid}/task/{tid}/stat')
            result['vcpu_thread_id']=tid
            result['process_before']=process_stat(statpath)
            result['vcpu_before']=process_stat(threadpath)
            begin=time.monotonic()
            gdb.start()
            running=True
            samples=[]
            consecutive=0
            while True:
                elapsed=time.monotonic()-begin
                if elapsed>10 or time.monotonic()-started>25:
                    raise TimeoutError('Original10s workload/25s observation deadline')
                sample=process_stat(threadpath)
                samples.append({'elapsed_seconds':elapsed,**sample})
                # No QMP/GDB pause/input while the finite loop executes.
                # Sleeping WFI is only a completion hint; exact state below
                # is mandatory, so a stall cannot count as a completed run.
                consecutive=consecutive+1 if sample['state']=='S' and elapsed>=.005 else 0
                if consecutive>=3:
                    break
                time.sleep(.001)
            result['process_after']=process_stat(statpath)
            result['vcpu_after']=process_stat(threadpath)
            result['observed_wall_seconds']=time.monotonic()-begin
            result['completion_samples']=samples
            result['stop_reply']=stop()
            state=gdb.state()
            result['terminal_state']=state
            expected={'regs':[0]*16,'cpsr':0x60000093}
            expected['regs'][0]=args.iterations
            expected['regs'][14]=0x1c14
            expected['regs'][15]=0xc
            result['expected_terminal_state']=expected
            result['closed_form_user_instruction_count']=4*args.iterations
            if state!=expected:
                raise RuntimeError('Full actual terminal state differs from closed-form oracle')
            for kind in ['process','vcpu']:
                before=result[kind+'_before'];after=result[kind+'_after']
                if before['starttime_ticks']!=after['starttime_ticks']:
                    raise RuntimeError('CPU identity changed')
                ticks=sum(after[k] - before[k] for k in ['utime_ticks','stime_ticks'])
                result[kind+'_cpu_ticks']=ticks
                result[kind+'_cpu_seconds']=ticks/result['ticks_per_second']
            result['status']='FINITE_THROUGHPUT_STATE_OBSERVED'
        except Exception as error:
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
            if result['exit_code']!=0:
                result['status']='FAILED'
            try:
                result['aggregate_witnesses']=summaries(root/'engine.log')
            except Exception as error:
                result['aggregate_witnesses']=[]
                result['status']='FAILED'
                result['error']=str(error)
            logtext=(root/'engine.log').read_text(errors='replace')
            if logtext and not logtext.endswith('\n'):
                result['status']='FAILED'
                result['error']='Incomplete final engine log record'
            if 'LT_DYNARMIC_BLOCK ' in logtext or 'LT_DYNARMIC_PROBE ' in logtext:
                result['status']='FAILED'
                result['witness_error']='Unexpected per-instruction/block diagnostic logging'
            ws=result['aggregate_witnesses']
            if args.require_throughput:
                n=args.iterations
                completed=0
                valid=bool(ws)
                previous_last=0
                for w in ws:
                    completed+=w['blocks']
                    last=completed==n
                    valid &= (0<w['blocks'] and completed<=n and
                              w['instructions']==4*w['blocks'] and
                              w['cold']==1 and w['warm']==w['blocks']-1 and
                              w['pc']==(0x1c10 if last else 0x1c00) and
                              w['r0']==completed and w['r1']==n-completed and
                              w['cpsr']==(0x60000010 if last else 0x20000010) and
                              previous_last<=w['first_us']<=w['cold_end_us']<=w['last_us'])
                    previous_last=w['last_us']
                valid &= completed==n
                result['aggregate_totals']={
                    'segments':len(ws),'blocks':completed,
                    'instructions':sum(w['instructions'] for w in ws),
                    'cold':sum(w['cold'] for w in ws),
                    'warm':sum(w['warm'] for w in ws)}
                if not valid:
                    result['status']='FAILED'
                    result['witness_error']='Required exact aggregate execution witness missing or different'
            elif ws:
                result['status']='FAILED'
                result['witness_error']='OFF unexpectedly executed alternate throughput path'
            result['duration_seconds']=time.monotonic()-started
            (root/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='FINITE_THROUGHPUT_STATE_OBSERVED' else 1


if __name__=='__main__':
    raise SystemExit(main())
