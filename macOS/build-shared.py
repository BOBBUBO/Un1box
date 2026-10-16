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
import re
import shlex
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


def runtime_provenance(runtime):
    """Bind an exported runtime to its provider files before native relocation."""
    path = runtime / "RUNTIME-MANIFEST.json"
    if not path.exists():
        return {}, {}
    manifest = json.loads(path.read_text())
    if manifest.get("format") != "lighttouch-macos-python-runtime" or manifest.get("version") != 1:
        raise ValueError("Unsupported Python runtime provenance.")
    origins = {}
    for name, value in manifest["files"].items():
        local = runtime / name
        if name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("Invalid runtime provenance destination.")
        original = Path(value["source"])
        if local.is_symlink() or not local.is_file() or sha(local) != value["sha256"]:
            raise ValueError("Exported runtime no longer matches its provenance: " + name)
        if original.is_symlink() or not original.is_file() or sha(original) != value["sha256"]:
            raise ValueError("Runtime provider no longer matches reviewed input: " + name)
        origins[local] = original.resolve()
    environment = {}
    for key, value in manifest["environment"].items():
        if key not in ("TCL_LIBRARY", "TK_LIBRARY") or value.startswith("/") or any(p in ("", ".", "..") for p in value.split("/")):
            raise ValueError("Invalid runtime script environment.")
        environment[key] = value
    if set(environment) != {"TCL_LIBRARY", "TK_LIBRARY"}:
        raise ValueError("Runtime Tcl/Tk script roots are required.")
    expected = set(manifest["files"]) | set(manifest["links"]) | {"RUNTIME-MANIFEST.json"}
    actual = {str(p.relative_to(runtime)) for p in runtime.rglob("*") if p.is_file() or p.is_symlink()}
    if expected != actual:
        raise ValueError("Exported runtime contains unmanifested/missing inputs.")
    for name, target in manifest["links"].items():
        if not (runtime / name).is_symlink() or os.readlink(runtime / name) != target:
            raise ValueError("Exported runtime link differs from its provenance.")
    return origins, environment


def load_minimum_macos(text):
    """Read actual deployment floors, rather than the SDK or host OS version."""
    versions = []
    for command in re.split(r"(?m)^Load command \d+\s*$", text)[1:]:
        if re.search(r"(?m)^\s*cmd LC_BUILD_VERSION\s*$", command):
            platform = re.search(r"(?m)^\s*platform (\S+)", command)
            minimum = re.search(r"(?m)^\s*minos (\S+)", command)
            if not platform or platform.group(1) not in ("1", "MACOS", "macos") or not minimum:
                raise ValueError("Only native macOS Mach-O dependencies are supported.")
            versions.append(minimum.group(1))
        elif re.search(r"(?m)^\s*cmd LC_VERSION_MIN_MACOSX\s*$", command):
            minimum = re.search(r"(?m)^\s*version (\S+)", command)
            if not minimum:
                raise ValueError("Malformed macOS minimum-version command.")
            versions.append(minimum.group(1))
    if not versions or any(not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value) for value in versions):
        raise ValueError("Every native dependency must declare a macOS deployment floor.")
    return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


def minimum_macos(versions):
    return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))


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
              "--configuration", "debug", "--jobs", "1", "--disable-index-store", "-Xswiftc", "-j1"]
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


def stage_swift_resources(binaries, contents, runtime_contexts):
    """Put SwiftPM resources beside the *observed* command-line main bundles.

    Native tools live under Helpers, outside the app's main MacOS executable
    directory. Reject any Foundation lookup outside Contents rather than making
    an invalid app-root layout or relying on the original SwiftPM scratch path.
    """
    origins = {}
    for bundle in sorted(Path(binaries).glob("*.bundle")):
        if bundle.is_symlink() or not bundle.is_dir():
            raise ValueError("Expected a real SwiftPM resource bundle.")
        for path in bundle.rglob("*"):
            if path.is_symlink() and bundle.resolve() not in path.resolve().parents:
                raise ValueError("SwiftPM resource link escapes its bundle.")
        if list(native_files(bundle)):
            raise ValueError("SwiftPM code bundles need an explicit native dependency audit.")
        target = contents / "Resources/SwiftPM" / bundle.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle, target, symlinks=True)
        for source in bundle.rglob("*"):
            if source.is_file():
                origins[str((target / source.relative_to(bundle)).relative_to(contents))] = {
                    "path": str(source), "input_sha256": sha(source)}
        for tool, context in runtime_contexts.items():
            root = Path(context["bundle_url"])
            if context.get("format") != "lighttouch-native-runtime-info" or context.get("version") != 1 or context.get("guest_started") is not False:
                raise ValueError("Native resource diagnostic returned an invalid receipt.")
            if root != root.resolve() or contents.resolve() not in root.resolve().parents or not root.is_dir():
                raise ValueError("Native resource lookup must stay inside app Contents: " + str(root))
            alias = root / bundle.name
            if alias.is_symlink() and alias.resolve() == target.resolve():
                continue  # Both CLI tools may use the same Foundation context.
            if alias.exists() or alias.is_symlink():
                raise ValueError("SwiftPM resource alias would overwrite an input.")
            alias.symlink_to(os.path.relpath(target, alias.parent))
            if alias.resolve() != target.resolve():
                raise ValueError("Native SwiftPM resource alias did not relocate.")
    return origins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "scratch", "engine", "firmwarekit", "python-runtime", "vendor-contents"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--engine-revision", required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Native macOS/Swift6.2+/SDL2 required; the app floor follows all linked dependencies.")
    output, scratch = private_path(args.output), private_path(args.scratch)
    if output.exists() or output.is_symlink() or output.suffix != ".app":
        parser.error("Use an absent .app output; existing apps are never replaced.")
    if output.resolve() == scratch.resolve() or output.resolve() in scratch.resolve().parents or scratch.resolve() in output.resolve().parents:
        parser.error("Scratch and app output must be separate directories.")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime = python_runtime(args.python_runtime)
    runtime_origins, script_environment = runtime_provenance(runtime)
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
            for name in ("MacOS", "Helpers", "Frameworks", "Resources"):
                (contents / name).mkdir(parents=True, exist_ok=True)
            originals = {}
            def copy(original, target):
                original = Path(original).resolve()
                shutil.copy2(original, target)
                originals[target] = original
                receipt["origins"][str(target.relative_to(contents))] = {"path": str(original), "input_sha256": sha(original)}
            for name in ("LightTouchDesktopHost", "LightTouchDevice"):
                copy(binaries / name, contents / "Helpers" / name)
            copy(engine, contents / "Frameworks/libqemu-arm.dylib")
            copy(firmwarekit, contents / "MacOS/firmwarekit")
            packaged_runtime = contents / "Frameworks/Python"
            stage_runtime(runtime, packaged_runtime)
            runtime_targets = {}
            for path in native_files(packaged_runtime):
                unrelocated = runtime / path.relative_to(packaged_runtime)
                original = runtime_origins.get(unrelocated, unrelocated)
                originals[path] = original
                if original.resolve() in runtime_targets:
                    raise ValueError("Duplicate native runtime provider destination.")
                runtime_targets[original.resolve()] = path
            stage_python(SOURCE, contents)

            # Relocate the real Mach-O dependency closure, including Python's
            # Tk extension. Reject ambiguous install names instead of silently
            # taking another installed library with the same filename.
            python_executable = (runtime / "bin/python3").resolve()
            provider_executable = runtime_origins.get(python_executable, python_executable)
            executable_dirs = {binaries, firmwarekit.parent, provider_executable.parent}
            pending = list(originals.items()); seen = set(); external = {}; minimum_versions = {}
            while pending:
                local, original = pending.pop()
                if local in seen:
                    continue
                seen.add(local)
                lines = subprocess.check_output(["otool", "-L", str(original)], text=True).splitlines()[1:]
                load_commands = subprocess.check_output(["otool", "-l", str(original)], text=True)
                rpaths = load_rpaths(load_commands)
                minimum_versions[str(local.relative_to(contents))] = load_minimum_macos(load_commands)
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
                    if candidate in runtime_targets:
                        target = runtime_targets[candidate]
                    elif runtime in candidate.parents:
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
export PYTHONDONTWRITEBYTECODE=1
unset TCL_LIBRARY TK_LIBRARY TCLLIBPATH PYTHONUSERBASE PYTHONPLATLIBDIR
''' + ''.join('export ' + key + '="$PYTHONHOME"/' + shlex.quote(value) + '\n' for key, value in script_environment.items()) + '''\
exec "$PYTHONHOME/bin/python3" -B -s "$LIGHTTOUCH_CONTENTS/Resources/launcher.py" "$@"
''')
            launcher.chmod(0o755)
            info = {"CFBundleExecutable": "LightTouch", "CFBundleIdentifier": "org.lighttouch.shared-desktop",
                    "CFBundleName": "LightTouch Shared Desktop", "CFBundlePackageType": "APPL",
                    "CFBundleVersion": "1", "CFBundleShortVersionString": "0.1", "LSMinimumSystemVersion": minimum_macos(minimum_versions.values()),
                    "NSHighResolutionCapable": True, "NSSupportsAutomaticGraphicsSwitching": True}
            (contents / "Info.plist").write_bytes(plistlib.dumps(info))
            receipt["native_minimum_macos"] = minimum_versions
            receipt["minimum_macos"] = info["LSMinimumSystemVersion"]
            receipt["runtime_manifest_sha256"] = sha(runtime / "RUNTIME-MANIFEST.json") if runtime_origins else None
            for binary in native_files(contents):
                command = ["codesign", "--force", "--sign", "-"]
                if binary == contents / "Helpers/LightTouchDevice":
                    command += ["--entitlements", str(SOURCE / "Configuration/LightTouchDevice.entitlements")]
                subprocess.run(command + [str(binary)], check=True)
            runtime_contexts = {}
            for name in ("LightTouchDesktopHost", "LightTouchDevice"):
                executable = contents / "Helpers" / name
                context = json.loads(subprocess.check_output([str(executable), "--runtime-info"],
                                    text=True, timeout=10, env=environment))
                if Path(context.get("executable_url", "")).resolve() != executable.resolve():
                    raise ValueError("Native diagnostic executable identity mismatch.")
                runtime_contexts[name] = context
            receipt["native_resource_contexts"] = runtime_contexts
            receipt["origins"].update(stage_swift_resources(binaries, contents, runtime_contexts))
            # Sign nested framework bundles after their contained Mach-O code;
            # the outer app seal must include those final bundle signatures.
            frameworks = sorted((p for p in contents.rglob("*.framework") if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True)
            for framework in frameworks:
                subprocess.run(["codesign", "--force", "--sign", "-", str(framework)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(output)], check=True)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(output)], check=True)
            runtime_env = dict(os.environ, PYTHONHOME=str(packaged_runtime), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
            runtime_env.pop("PYTHONPATH", None)
            for key in ("TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH", "PYTHONUSERBASE", "PYTHONPLATLIBDIR"):
                runtime_env.pop(key, None)
            runtime_env.update({key: str(packaged_runtime / value) for key, value in script_environment.items()})
            subprocess.run([str(packaged_runtime / "bin/python3"), "-B", "-s", "-c",
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
