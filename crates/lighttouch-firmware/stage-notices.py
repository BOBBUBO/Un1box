#!/usr/bin/env python3
"""Stage the Rust firmwarekit's notices for an app that ships it (the Android APK's assets/licenses/firmwarekit).

    stage-notices.py OUT --target TRIPLE --source-reference URL

OUT gets the licence and notice files of every crate the binary links for TRIPLE (normal dependencies of
lighttouch-firmware, from `cargo metadata`) as <crate>-<version>-<file>, the vendored iBoot32Patcher's licence, a
NOTICE.txt naming what is compiled in from C, and manifest.json in the schema the app's licence screen reads
({"schema": 1, "source_reference", "files": [{"path", "sha256"}]}).
"""
import argparse, hashlib, json, re, shutil, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMS = re.compile(r"^(LICEN[SC]E|COPYING|NOTICE|COPYRIGHT|UNLICENSE)", re.I)

NOTICE = """firmwarekit (crates/lighttouch-firmware in LightTouch's source) prepares an emulated device from an IPSW.
It is written in Rust and links, statically:

- the Rust standard library (MIT or Apache-2.0);
- the crates whose licences follow, as <crate>-<version>-<file>; rusqlite's libsqlite3-sys compiles in SQLite
  (public domain, https://sqlite.org/copyright.html);
- iBoot32Patcher (LukeZGD's fork, GPL-3.0-or-later: iBoot32Patcher-LICENSE), which patches the iPad's iBoot. With it,
  firmwarekit as distributed is a combined work under the GNU GPL version 3 or later; LightTouch's own code in it is
  GPL-2.0-or-later. The source is at the reference in manifest.json;
- LightTouch's activation.c (GPL-2.0-or-later).
"""


def linked(target):
    meta = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--format-version", "1", "--filter-platform", target, "--locked"],
        cwd=HERE))
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = next(p["id"] for p in meta["packages"] if p["name"] == "lighttouch-firmware")
    seen, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for dep in nodes[pid]["deps"]:
            if any(k["kind"] is None for k in dep["dep_kinds"]):  # normal (linked) dependencies only
                todo.append(dep["pkg"])
    return [packages[p] for p in sorted(seen) if packages[p]["source"]]  # crates.io/git, not the workspace


def main():
    a = argparse.ArgumentParser()
    a.add_argument("out", type=Path)
    a.add_argument("--target", required=True)
    a.add_argument("--source-reference", required=True)
    args = a.parse_args()
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    staged = {"NOTICE.txt": NOTICE.encode()}
    staged["iBoot32Patcher-LICENSE"] = (HERE / "vendor/iBoot32Patcher/LICENSE").read_bytes()
    for p in linked(args.target):
        root = Path(p["manifest_path"]).parent
        terms = sorted(f for f in root.iterdir() if f.is_file() and TERMS.match(f.name))
        if not terms:
            raise SystemExit(f"stage-notices: {p['name']} {p['version']} ({p.get('license')}) ships no licence file")
        for f in terms:
            staged[f"{p['name']}-{p['version']}-{f.name}"] = f.read_bytes()
    files = []
    for path, data in sorted(staged.items()):
        (args.out / path).write_bytes(data)
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"schema": 1, "source_reference": args.source_reference, "files": files}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(files)} notice files in {args.out}")


if __name__ == "__main__":
    main()
