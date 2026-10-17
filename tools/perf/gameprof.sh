#!/bin/bash
# Profile the in-app engine while a game runs on its own (no injected input).
# usage: ANDROID_SERIAL=... gameprof.sh OUTDIR [seconds]
set -u
OUT=$1; SECS=${2:-15}; mkdir -p "$OUT"; PKG=org.lighttouch.android
pid=$(adb shell pidof $PKG:engine | tr -d '\r')
adb shell "run-as $PKG sh -c 'cd /data/data/$PKG && simpleperf record -p $pid -m 8 --cpu-percent 25 -f 4000 --duration $SECS -o files/perf.data' >/dev/null 2>&1"
r() { adb shell "run-as $PKG sh -c 'cd /data/data/$PKG && simpleperf report -i files/perf.data $1'"; }
r "--sort tid,comm --percent-limit 1" > "$OUT/threads.txt" 2>&1
r "--comms \"CPU 0/TCG\" --sort dso --percent-limit 0.5" > "$OUT/vcpu-dso.txt" 2>&1
r "--comms \"CPU 0/TCG\" --sort symbol --percent-limit 0.4" > "$OUT/vcpu-sym.txt" 2>&1
r "--sort comm,dso --percent-limit 1" > "$OUT/comm-dso.txt" 2>&1
echo done "$OUT"
# Host-side symbols: pull the data, then e.g.
#   $NDK/simpleperf/binary_cache_builder.py -i OUT/perf.data -lib BUILD_DIR
#   $NDK/simpleperf/report.py? or: simpleperf report -i OUT/perf.data --symfs binary_cache --sort symbol
adb exec-out run-as $PKG cat files/perf.data > "$OUT/perf.data"
