#!/usr/bin/env python3
"""Build the shared Tk/SDL macOS development app on its native host.

Requires an explicitly supplied self-contained Python/Tk runtime. No installed
library, guest package, firmware, writable state or SDK is copied into the app.
Run under a separately allocated finite native-build supervisor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]
MACH_MAGICS = {bytes.fromhex(v) for v in ("cffaedfe", "cefaedfe", "feedfacf", "feedface", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            value.update(block)
    return value.hexdigest()


def private_path(path, source=SOURCE):
    value = Path(path).expanduser().absolute()
    canonical = value.resolve()
    source = Path(source).resolve()
    if canonical == source or source in canonical.parents or canonical in source.parents:
        raise ValueError("Outputs and scratch must be outside the source checkout.")
    return value


def python_runtime(root):
    root = Path(root).resolve()
    executable = root / "bin/python3"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("Supply a self-contained Python/Tk runtime with executable bin/python3.")
    for path in root.rglob("*"):
        if path.is_symlink() and root not in path.resolve().parents:
            raise ValueError("Python runtime links must stay inside that runtime: " + str(path))
    return root


def stage_runtime(runtime, target):
    runtime = python_runtime(runtime)
    shutil.copytree(runtime, target, symlinks=True)
    # Internal absolute links must point into the copied runtime after moving.
    for original in runtime.rglob("*"):
        if original.is_symlink():
            local = target / original.relative_to(runtime)
            destination = target / original.resolve().relative_to(runtime)
            local.unlink()
            local.symlink_to(os.path.relpath(destination, local.parent))


def resolve_dependency(install, original, runtime, vendor, executable_dirs, rpaths=()):
    """Resolve real Mach-O search paths without guessing a dylib's process.

    A dylib's @executable_path belongs to its loading executable, not the dylib.
    Ambiguous contexts are rejected so a packaging pass cannot conceal a wrong
    installed dependency with the same basename.
    """
    original, runtime, vendor = Path(original), Path(runtime), Path(vendor)
    def expand(path):
        if path.startswith("@loader_path/"):
            return [original.parent / path.removeprefix("@loader_path/")]
        if path == "@loader_path":
            return [original.parent]
        if path.startswith("@executable_path/"):
            return [p / path.removeprefix("@executable_path/") for p in executable_dirs]
        if path == "@executable_path":
            return list(executable_dirs)
        if path.startswith("@"):
            return []
        return [Path(path)]
    if install.startswith("@rpath/"):
        suffix = install.removeprefix("@rpath/")
        roots = [runtime / "lib", vendor / "Frameworks", original.parent]
        for rpath in rpaths:
            roots.extend(expand(rpath))
        choices = [root / suffix for root in roots]
    else:
        choices = expand(install)
    matches = {path.resolve() for path in choices if path.is_file()}
    if len(matches) != 1:
        raise ValueError("Dependency requires explicit unambiguous input: " + install)
    return matches.pop()


def load_rpaths(text):
    result = []
    lines = iter(text.splitlines())
    for line in lines:
        if line.strip() == "cmd LC_RPATH":
            for field in lines:
                if field.strip().startswith("path "):
                    result.append(field.strip()[5:].rsplit(" (offset ", 1)[0]); break
                if field.startswith("Load command "):
                    raise ValueError("Malformed LC_RPATH load command.")
    return result


def compile_commands(source, scratch):
    common = ["swift", "build", "--package-path", str(source), "--scratch-path", str(scratch),
              "--configuration", "debug", "--jobs", "1"]
    return [common + ["--product", name] for name in ("LightTouchDesktopHost", "LightTouchDevice")]


def stage_python(source, contents):
    """Copy exact shared widgets/domain and the thin Mac entrypoint, no assets."""
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "Desktop/lighttouch_desktop", resources / "lighttouch_desktop",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(source / "macOS/launcher.py", resources / "launcher.py")


def native_files(root):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACH_MAGICS:
                    yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "scratch", "engine", "firmwarekit", "python-runtime", "vendor-contents"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--engine-revision", required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Native macOS14.4+/Swift6.2/SDL2 required.")
    output, scratch = private_path(args.output), private_path(args.scratch)
    if output.exists() or output.is_symlink() or output.suffix != ".app":
        parser.error("Use an absent .app output; existing apps are never replaced.")
    if output.resolve() == scratch.resolve() or output.resolve() in scratch.resolve().parents or scratch.resolve() in output.resolve().parents:
        parser.error("Scratch and app output must be separate directories.")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = python_runtime(args.python_runtime)
    engine, firmwarekit, vendor = args.engine.resolve(), args.firmwarekit.resolve(), args.vendor_contents.resolve()
    if not engine.is_file() or not firmwarekit.is_file():
        parser.error("Native engine and firmware admission worker are required explicit inputs.")
    subprocess.run(["pkg-config", "--exists", "sdl2"], check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=SOURCE, text=True):
        parser.error("Commit the exact source before a recorded shared build.")
    receipt = {"schema": 1, "status": "RUNNING", "source_revision": revision,
               "engine_revision": args.engine_revision, "shared_ui": True, "guest_acceptance": False,
               "compile_argv": compile_commands(SOURCE, scratch), "origins": {},
               "limitations": ["development/ad-hoc signing", "shared Mac target execution unqualified",
                               "guest app services and native process metrics not implemented"]}
    receipt_path = output.with_suffix(".build-receipt.json")
    fd = os.open(str(receipt_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as report:
        try:
            environment = dict(os.environ, LIGHTTOUCH_MAC_VENDOR_CONTENTS=str(vendor))
            for command in receipt["compile_argv"]:
                subprocess.run(command, env=environment, check=True)
            common = receipt["compile_argv"][0][:-2]
            binaries = Path(subprocess.check_output(common + ["--show-bin-path"], env=environment, text=True).strip())
            contents = output / "Contents"
            for name in ("MacOS", "Frameworks", "Resources"):
                (contents / name).mkdir(parents=True, exist_ok=True)
            originals = {}
            def copy(original, target):
                original = Path(original).resolve()
                shutil.copy2(original, target)
                originals[target] = original
                receipt["origins"][str(target.relative_to(contents))] = {"path": str(original), "input_sha256": sha(original)}
            for name in ("LightTouchDesktopHost", "LightTouchDevice"):
                copy(binaries / name, contents / "MacOS" / name)
            copy(engine, contents / "Frameworks/libqemu-arm.dylib")
            copy(firmwarekit, contents / "MacOS/firmwarekit")
            packaged_runtime = contents / "Frameworks/Python"
            stage_runtime(runtime, packaged_runtime)
            for path in native_files(packaged_runtime):
                originals[path] = runtime / path.relative_to(packaged_runtime)
            stage_python(SOURCE, contents)

            # Relocate the real Mach-O dependency closure, including Python's
            # Tk extension. Reject ambiguous install names instead of silently
            # taking another installed library with the same filename.
            executable_dirs = {binaries, firmwarekit.parent, runtime / "bin", (runtime / "bin/python3").resolve().parent}
            pending = list(originals.items()); seen = set(); external = {}
            while pending:
                local, original = pending.pop()
                if local in seen:
                    continue
                seen.add(local)
                lines = subprocess.check_output(["otool", "-L", str(original)], text=True).splitlines()[1:]
                rpaths = load_rpaths(subprocess.check_output(["otool", "-l", str(original)], text=True))
                # The first -L entry of a dylib is its install ID, even if the
                # original runtime is no longer installed at that old path.
                ids = subprocess.run(["otool", "-D", str(original)], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False).stdout.splitlines()[1:]
                install_ids = {line.strip() for line in ids}
                for line in lines:
                    install = line.strip().split(" (", 1)[0]
                    if install in install_ids or install.startswith(("/usr/lib/", "/System/Library/")):
                        continue
                    candidate = resolve_dependency(install, original, runtime, vendor, executable_dirs, rpaths)
                    if runtime in candidate.parents:
                        target = packaged_runtime / candidate.relative_to(runtime)
                    else:
                        target = contents / "Frameworks" / candidate.name
                        if candidate.name in external and external[candidate.name] != candidate:
                            raise ValueError("Colliding native dependency filename: " + candidate.name)
                        if candidate.name not in external:
                            if target.exists():
                                raise ValueError("Dependency would overwrite a staged input: " + str(target))
                            copy(candidate, target); external[candidate.name] = candidate
                            pending.append((target, candidate))
                    relative = "@loader_path/" + os.path.relpath(target, local.parent)
                    subprocess.run(["install_name_tool", "-change", install, relative, str(local)], check=True)

            launcher = contents / "MacOS/LightTouch"
            launcher.write_text('''#!/bin/sh
set -eu
LIGHTTOUCH_CONTENTS="$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)"
unset PYTHONPATH PYTHONSTARTUP PYTHONINSPECT
export PYTHONHOME="$LIGHTTOUCH_CONTENTS/Frameworks/Python"
export PYTHONNOUSERSITE=1
exec "$PYTHONHOME/bin/python3" -s "$LIGHTTOUCH_CONTENTS/Resources/launcher.py" "$@"
''')
            launcher.chmod(0o755)
            info = {"CFBundleExecutable": "LightTouch", "CFBundleIdentifier": "org.lighttouch.shared-desktop",
                    "CFBundleName": "LightTouch Shared Desktop", "CFBundlePackageType": "APPL",
                    "CFBundleVersion": "1", "CFBundleShortVersionString": "0.1", "LSMinimumSystemVersion": "14.4",
                    "NSHighResolutionCapable": True, "NSSupportsAutomaticGraphicsSwitching": True}
            (contents / "Info.plist").write_bytes(plistlib.dumps(info))
            for binary in native_files(contents):
                command = ["codesign", "--force", "--sign", "-"]
                if binary == contents / "MacOS/LightTouchDevice":
                    command += ["--entitlements", str(SOURCE / "Configuration/LightTouchDevice.entitlements")]
                subprocess.run(command + [str(binary)], check=True)
            # Sign nested framework bundles after their contained Mach-O code;
            # the outer app seal must include those final bundle signatures.
            frameworks = sorted((p for p in contents.rglob("*.framework") if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True)
            for framework in frameworks:
                subprocess.run(["codesign", "--force", "--sign", "-", str(framework)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(output)], check=True)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(output)], check=True)
            runtime_env = dict(os.environ, PYTHONHOME=str(packaged_runtime), PYTHONNOUSERSITE="1")
            runtime_env.pop("PYTHONPATH", None)
            subprocess.run([str(packaged_runtime / "bin/python3"), "-s", "-c",
                            "import tkinter; import sys; print(sys.version); print(tkinter.TkVersion)"],
                           env=runtime_env, check=True)
            receipt["files"] = {str(p.relative_to(contents)): sha(p) for p in contents.rglob("*") if p.is_file()}
            receipt["status"] = "PASS"
        except BaseException as error:
            receipt.update(status="FAIL", failure=str(error))
            raise
        finally:
            report.write(json.dumps(receipt, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
