#!/usr/bin/env python3
# bqlsites.py LOG T0 T1: vCPU BQL takes/waits per site between two accounting dumps
import re,sys
log,t0,t1=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
dumps={};cur=None
for l in open(log,errors='replace'):
    m=re.search(r"\[acct\]\s+([\d.]+)s\s+run",l)
    if m: cur=float(m.group(1)); dumps[cur]={}; continue
    m=re.search(r"\[acct\]   vcpu-bql n (\d+) wait-ns (\d+)",l)
    if m and cur is not None: dumps[cur]['TOTAL']=(int(m.group(1)),int(m.group(2))); continue
    m=re.search(r"\[acct\]   bql-by-mr (\S+) n (\d+) wait-ns (\d+)",l)
    if m and cur is not None:
        k=re.sub(r'^.*qemu-ios(?:-android)?/','',m.group(1))
        n,w=dumps[cur].get(k,(0,0)); dumps[cur][k]=(n+int(m.group(2)),w+int(m.group(3)))
a=dumps[min(dumps,key=lambda t:abs(t-t0))]; b=dumps[min(dumps,key=lambda t:abs(t-t1))]; dt=t1-t0
rows=[(k,(b[k][0]-a.get(k,(0,0))[0])/dt,(b[k][1]-a.get(k,(0,0))[1])/dt/1e7) for k in b]
for k,n,w in sorted(rows,key=lambda r:-r[2])[:12]: print(f"{k:60s} {n:8.0f}/s  wait {w:5.1f}%  mean {w*1e7/max(n,1)/1e3:5.1f} us")
