#!/usr/bin/env python3
"""Plan/export an explicit Python framework + Tk runtime, without installing it.

Planning only reads the named providers. Export requires the reviewed manifest
hash, writes a new private directory, and never copies external site-packages.
Native install names are relocated by build-shared.py, not by this exporter.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

FORMAT = "lighttouch-macos-python-runtime"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Runtime destination must be a normalized relative path.")
    return path


def plan_runtime(prefix, version, tkinter, tcl, tk):
    if not version.startswith("3.") or not all(p.isdigit() for p in version.split(".")) or len(version.split(".")) != 2:
        raise ValueError("Use an explicit Python major.minor version.")
    prefix, tkinter, tcl, tk = (Path(p).resolve(strict=True) for p in (prefix, tkinter, tcl, tk))
    if not (tcl / "init.tcl").is_file() or not (tk / "tk.tcl").is_file():
        raise ValueError("Explicit Tcl init.tcl and Tk tk.tcl script roots are required.")
    if not tkinter.name.startswith("_tkinter.") or tkinter.suffix != ".so":
        raise ValueError("Supply the matching explicit _tkinter extension.")
    entries = {}

    def add(source, destination):
        destination = str(relative_path(destination))
        if destination in entries:
            raise ValueError("Duplicate runtime destination: " + destination)
        source = Path(source)
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Expected regular runtime input: " + str(source))
        entries[destination] = {"source": str(source), "bytes": info.st_size,
                                "sha256": sha(source), "mode": stat.S_IMODE(info.st_mode) & 0o755}

    def tree(root, destination, excluded=frozenset()):
        # Explicit roots only. Resolve contained links into separately hashed
        # regular files so no provider/Cellar symlink survives relocation.
        seen = set()
        def visit(directory, target):
            identity = directory.resolve(strict=True)
            if identity != root and root not in identity.parents:
                raise ValueError("Runtime scripts/stdlib link escapes its explicit root: " + str(directory))
            if identity in seen:
                raise ValueError("Cyclic runtime input directory: " + str(directory))
            seen.add(identity)
            for path in sorted(directory.iterdir()):
                if path.name in excluded or path.name == "__pycache__" or path.suffix in (".pyc", ".pyo"):
                    continue
                resolved = path.resolve(strict=True)
                if resolved != root and root not in resolved.parents:
                    raise ValueError("Runtime input link escapes its explicit root: " + str(path))
                if resolved.is_dir():
                    visit(resolved, target + "/" + path.name)
                else:
                    add(resolved, target + "/" + path.name)
            seen.remove(identity)
        visit(root, destination)

    add(prefix / "bin" / ("python" + version), "bin/python" + version)
    add(prefix / "Python", "Python")
    stdlib = "lib/python" + version
    tree(prefix / stdlib, stdlib, frozenset(("site-packages", "test", "tests", "ensurepip")))
    add(tkinter, stdlib + "/lib-dynload/" + tkinter.name)
    tree(tcl, "lib/" + tcl.name)
    tree(tk, "lib/" + tk.name)
    if not any(p.startswith(stdlib + "/encodings/") for p in entries) or stdlib + "/tkinter/__init__.py" not in entries:
        raise ValueError("Python stdlib must include encodings and tkinter.")
    return {"format": FORMAT, "version": 1, "python_version": version,
            "native_relocation_required": True, "redistribution_license_audit_required": True,
            "files": entries, "links": {"bin/python3": "python" + version},
            "environment": {"TCL_LIBRARY": "lib/" + tcl.name, "TK_LIBRARY": "lib/" + tk.name},
            "excluded": ["external site-packages", "test suites", "ensurepip", "bytecode", "unrelated provider files"]}


def validate_manifest(manifest):
    if manifest.get("format") != FORMAT or manifest.get("version") != 1:
        raise ValueError("Unsupported runtime manifest.")
    files, links = manifest["files"], manifest["links"]
    for name in (*files, *links):
        relative_path(name)
        if any(str(parent) in files or str(parent) in links for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("Runtime destination descends through a file/link.")
    if set(files) & set(links):
        raise ValueError("Runtime file and link destinations collide.")
    for name, value in links.items():
        link = relative_path(value)
        target = str(PurePosixPath(name).parent / link)
        if target not in files:
            raise ValueError("Runtime link must name an explicit regular input.")
    for key, value in manifest["environment"].items():
        if key not in ("TCL_LIBRARY", "TK_LIBRARY"):
            raise ValueError("Unsupported runtime environment key.")
        relative_path(value)
    return manifest


def export_runtime(manifest, output):
    validate_manifest(manifest)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Runtime output must be absent.")
    # Rehash every provider before publication, including the Tk extension.
    for value in manifest["files"].values():
        source = Path(value["source"])
        if source.is_symlink() or not source.is_file() or source.stat().st_size != value["bytes"] or sha(source) != value["sha256"]:
            raise ValueError("Pinned runtime input changed: " + str(source))
    output.mkdir(mode=0o700, parents=False)
    try:
        for name, value in manifest["files"].items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(value["source"], target)
            target.chmod(value["mode"])
            if sha(target) != value["sha256"]:
                raise ValueError("Runtime changed during copy: " + name)
        for name, value in manifest["links"].items():
            (output / name).symlink_to(value)
        (output / "RUNTIME-MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except BaseException:
        # Remove only the newly created directory owned by this export.
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    for name in ("python-prefix", "tkinter", "tcl-scripts", "tk-scripts", "output-manifest"):
        plan.add_argument("--" + name, type=Path, required=True)
    plan.add_argument("--python-version", required=True)
    export = sub.add_parser("export")
    export.add_argument("--manifest", type=Path, required=True)
    export.add_argument("--manifest-sha256", required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = plan_runtime(args.python_prefix, args.python_version, args.tkinter, args.tcl_scripts, args.tk_scripts)
        with args.output_manifest.open("x") as stream:
            json.dump(result, stream, indent=2, sort_keys=True); stream.write("\n")
        print(sha(args.output_manifest))
    else:
        if sha(args.manifest) != args.manifest_sha256:
            raise ValueError("Runtime manifest differs from the reviewed hash.")
        export_runtime(json.loads(args.manifest.read_text()), args.output)


if __name__ == "__main__":
    main()
