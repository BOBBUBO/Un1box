#!/usr/bin/env python3
"""Compare directly marked guest tally candidates with other post-present intervals.

Use after framepath.py has validated the same sparse capture. This report reads
the complete event stream itself, checks completion and frame coverage, and
locates each shim-tally marker between adjacent native presents. The interval
includes guest work and scheduling; it is not a timer for stderr writes alone.
"""
import argparse
import bisect
from collections import Counter
import json
from pathlib import Path
import re

from framestats import analyse, parse, pct


def distribution(rows):
    values = [row["ms"] for row in rows]
    return dict(count=len(values), mean_ms=sum(values) / len(values) if values else None,
                median_ms=pct(values, 50) if values else None,
                p95_ms=pct(values, 95) if values else None,
                max_ms=max(values) if values else None,
                over40=sum(value > 40 for value in values))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("events", type=Path)
    ap.add_argument("frames", type=Path)
    ap.add_argument("--secs", type=float, default=60)
    ap.add_argument("--mode", choices=("off", "on"), required=True)
    ap.add_argument("--json", type=Path, required=True)
    args = ap.parse_args()
    lines = args.events.read_text().splitlines()
    if not lines or not lines[0].startswith("# LT_FRAME_PATH 1 "):
        ap.error("missing frame-path header")
    footer = re.fullmatch(r"# end records=(\d+) dropped=(\d+) incomplete=(\d+) end-ns=(\d+)",
                          lines[-1])
    if not footer or int(footer[2]) or int(footer[3]):
        ap.error("missing footer or dropped/incomplete events")
    events = []
    for line in lines[1:-1]:
        parts = line.split()
        if len(parts) != 5 or not all(parts[i].isdigit() for i in (0, 2, 3, 4)):
            ap.error("invalid event row")
        events.append(dict(ns=int(parts[0]), kind=parts[1],
                           a=int(parts[2]), b=int(parts[3]), c=int(parts[4])))
    if len(events) != int(footer[1]):
        ap.error("event/footer count mismatch")
    events.sort(key=lambda e: e["ns"])
    frames = parse(args.frames.read_text())
    if len(frames) < 2 or args.secs <= 0:
        ap.error("need frames and a positive duration")
    begin, end = frames[0][0], frames[0][0] + round(args.secs * 1e9)
    frames = [row for row in frames if row[0] < end]
    if len(frames) < 2:
        ap.error("need multiple frames inside the selected window")
    if any(b[0] <= a[0] or b[1] <= a[1] for a, b in zip(frames, frames[1:])):
        ap.error("non-increasing frame timestamps/sequences")
    if not events or events[0]["ns"] > begin or events[-1]["ns"] < frames[-1][0]:
        ap.error("events do not cover the frame window")
    publications = {}
    calls, pending, markers = [], None, []
    for event in events:
        kind = event["kind"]
        if kind == "publish":
            if event["a"] in publications:
                ap.error("reused publication sequence")
            publications[event["a"]] = event
        elif kind == "present-begin":
            if pending is not None:
                ap.error("overlapping native presents")
            pending = event
        elif kind == "present-end":
            if pending is None or (pending["a"], pending["b"]) != (event["a"], event["b"]):
                ap.error("unpaired native present")
            calls.append(dict(begin_ns=pending["ns"], end_ns=event["ns"],
                              context=event["a"], result=event["c"]))
            pending = None
        elif kind == "shim-tally":
            markers.append(event)
    if pending is not None:
        ap.error("unfinished native present")
    if not markers:
        ap.error("no direct shim-tally markers")
    if len({call["context"] for call in calls}) != 1:
        ap.error("this report requires one native present context")
    for ns, seq, *_ in frames:
        if seq not in publications or publications[seq]["ns"] > ns:
            ap.error("missing or late publication marker")
    intervals = [dict(begin_ns=a["end_ns"], end_ns=b["begin_ns"],
                      ms=(b["begin_ns"] - a["end_ns"]) / 1e6,
                      native_ordinal=i + 1, tally=None)
                 for i, (a, b) in enumerate(zip(calls, calls[1:]))]
    ends = [call["end_ns"] for call in calls]
    last_count = 0
    for marker in markers:
        count = marker["a"] + marker["b"]
        if count <= last_count or (count != 1 and count % 300) or marker["c"] != (args.mode == "on"):
            ap.error("invalid tally counter or mode")
        last_count = count
        index = bisect.bisect_right(ends, marker["ns"]) - 1
        if index < 0 or index >= len(intervals):
            ap.error("tally lacks adjacent native presents")
        row = intervals[index]
        if not row["begin_ns"] <= marker["ns"] <= row["end_ns"] or row["tally"] is not None:
            ap.error("tally overlaps a native call or another tally")
        row["tally"] = dict(ns=marker["ns"], ok=marker["a"], failed=marker["b"],
                            quiet=marker["c"], native_ordinal=row["native_ordinal"],
                            before_marker_ms=(marker["ns"] - row["begin_ns"]) / 1e6,
                            after_marker_ms=(row["end_ns"] - marker["ns"]) / 1e6)
    selected = [row for row in intervals if begin <= row["begin_ns"] and row["end_ns"] < end]
    candidates = [row for row in selected if row["tally"] and
                  row["tally"]["ok"] + row["tally"]["failed"] > 1]
    if not candidates:
        ap.error("no complete periodic candidate intervals in the window")
    controls = [row for row in selected if row["tally"] is None]
    hitches = [dict(begin_ns=a[0], end_ns=b[0], ms=(b[0] - a[0]) / 1e6,
                    engine_begin_ns=publications[a[1]]["ns"],
                    engine_end_ns=publications[b[1]]["ns"])
               for a, b in zip(frames, frames[1:]) if b[0] - a[0] > 50_000_000]
    for row in candidates:
        row["relative_marker_s"] = (row["tally"]["ns"] - begin) / 1e9
        row["overlapping_hitches"] = [hitch for hitch in hitches
                                     if row["begin_ns"] < hitch["engine_end_ns"] and
                                     row["end_ns"] > hitch["engine_begin_ns"]]
        row["marker_containing_hitches"] = [hitch for hitch in hitches
                                           if hitch["engine_begin_ns"] < row["tally"]["ns"] <=
                                           hitch["engine_end_ns"]]
    report = dict(mode=args.mode, window=dict(begin_ns=begin, end_exclusive_ns=end),
                  records=len(events), native_context=calls[0]["context"],
                  native_results=dict(Counter(call["result"] for call in calls)),
                  frames={k: v for k, v in analyse(frames, secs=args.secs).items()
                          if k in ("mean", "ceiling", "median", "low", "low1", "hitches",
                                   "hitch_ms", "skipped")},
                  candidates=distribution(candidates), controls=distribution(controls),
                  candidate_intervals=candidates, hitches=hitches,
                  slow_control_intervals=[row for row in controls if row["ms"] > 40],
                  limits="Post-present intervals include guest work and scheduling, not only logging. "
                         "Hitch overlap is temporal, not proof of causation. Sparse diagnostic, not ordinary FPS.")
    args.json.write_text(json.dumps(report, indent=2) + "\n")
    print("Frames:", report["frames"])
    print("Periodic tally intervals:", report["candidates"])
    print("Other intervals:", report["controls"])
    for row in candidates:
        tally = row["tally"]
        print(f"{row['relative_marker_s']:.3f}s: count={tally['ok'] + tally['failed']} "
              f"native={tally['native_ordinal']} interval={row['ms']:.3f}ms "
              f"after-marker={tally['after_marker_ms']:.3f}ms "
              f"interval-overlapping-hitches={len(row['overlapping_hitches'])} "
              f"marker-inside-hitches={len(row['marker_containing_hitches'])}")
    print(report["limits"])


if __name__ == "__main__":
    main()
