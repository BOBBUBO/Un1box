#!/usr/bin/env python3
"""Check the camera path without a build, a device or a CI run.

Every layer is exercised where it can be, and read where it cannot:

  ISP frame      real hw/arm/host-camera.c + the engine's own isp_from_camera(),
                 sliced out of hw/arm/s5l8930_isp.c: the newest frame wins, the
                 two sensors stay apart, and both output layouts scale correctly
  portable API   real lt_runtime_camera_write/info/flush/sensor, sliced out of
                 Portable/src/Runtime.cpp, against a stub engine
  JNI glue       real Native.cameraFrame/cameraFlush/cameraInfo, sliced out of
                 lighttouch_jni.cpp, against a stub JNIEnv: a planar and a
                 semi-planar ImageReader frame both become one NV12 frame
  wiring         the names, permissions, settings and orderings that connect
                 those layers
  engine patches the patch stack still applies to the engine and still commits
                 to a clean tree (needs --engine; skipped otherwise)

The engine side needs an engine checkout: --engine, $LT_ENGINE_SOURCE, or a
qemu-ios/ directory beside this repository. An unpatched checkout is copied out
of git into a temporary directory and the patches in ci/engine-patches are
applied there, so nothing in either tree is modified.

  python3 tests/camera/run.py [--engine PATH] [-v]
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "camera"
sys.path.insert(0, str(HERE))

import slice as slicer  # noqa: E402
import wiring  # noqa: E402

PATCHES = ROOT / "ci" / "engine-patches"


class Failure(RuntimeError):
    pass


def sh(command, cwd=None, stdin=None):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, input=stdin)


def engine_from_git(path: Path, scratch: Path) -> Path:
    """A copy of the engine at HEAD with ci/engine-patches applied."""
    archive = subprocess.run(["git", "-C", str(path), "archive", "HEAD"], capture_output=True)
    if archive.returncode:
        raise Failure(f"git archive failed in {path}: {archive.stderr.decode(errors='replace').strip()}")
    target = scratch / "engine"
    target.mkdir()
    extract = subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, capture_output=True)
    if extract.returncode:
        raise Failure(f"could not unpack the engine: {extract.stderr.decode(errors='replace').strip()}")
    patches = sorted(PATCHES.glob("*.patch"))
    for patch in patches:
        applied = sh(["git", "apply", str(patch)], cwd=target)
        if applied.returncode:
            # The camera patch is a delta on 0009, not on HEAD, so there is no single-patch
            # fallback here: a stack that does not fit means the engine revision moved.
            raise Failure(f"{patch.name} does not apply to {path} at HEAD: {applied.stderr.strip()}")
    return target


def resolve_engine(explicit: str | None, scratch: Path) -> tuple[Path | None, str]:
    candidate = explicit or __import__("os").environ.get("LT_ENGINE_SOURCE")
    path = Path(candidate).expanduser().resolve() if candidate else ROOT.parent / "qemu-ios"
    if path.is_dir() and (path / "hw/arm/s5l8930_isp.c").is_file():
        text = (path / "hw/arm/s5l8930_isp.c").read_text(errors="ignore")
        if "host_camera_take" in text:
            return path, f"engine tree {path} (already patched)"
        if (path / ".git").exists() or sh(["git", "-C", str(path), "rev-parse", "--git-dir"]).returncode == 0:
            return engine_from_git(path, scratch), f"engine tree {path} at HEAD + ci/engine-patches"
        raise Failure(f"{path} is an engine tree with our patches and no way to make one")
    if candidate:
        raise Failure(f"{path} is not an engine checkout (no hw/arm/s5l8930_isp.c)")
    return None, "no engine checkout: device-side slices skipped"


class Report:
    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, name, ok, detail=""):
        self.rows.append((name, ok, detail))
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + (f" — {detail}" if detail and (not ok or self.verbose) else ""))
        return ok

    def run(self, name, action):
        try:
            detail = action() or ""
            return self.add(name, True, detail)
        except Failure as failure:
            return self.add(name, False, str(failure))
        except Exception as failure:  # a crash in a check is a failed check
            return self.add(name, False, f"{type(failure).__name__}: {failure}")

    @property
    def failed(self):
        return [row for row in self.rows if not row[1]]


def compile_and_run(report, name, test_file, compiler, flags, sources, includes, scratch):
    generated = scratch / (test_file.stem + ".gen" + test_file.suffix)
    binary = scratch / test_file.stem
    _, own_flags, own_libs = slicer.generate(test_file, ROOT, ENGINE, generated)
    command = [compiler, "-std=gnu11" if compiler.endswith("cc") else "-std=gnu++20", "-O1", "-g",
               *own_flags, *flags]
    if SANITIZE:
        command += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    for include in includes:
        command += ["-I", str(include)]
    command += [str(generated), *map(str, sources), "-o", str(binary), *own_libs]
    built = sh(command)
    if built.returncode:
        raise Failure("compile failed:\n" + built.stderr.strip()[-4000:])
    ran = sh([str(binary)])
    if ran.returncode:
        raise Failure("failed:\n" + (ran.stdout + ran.stderr).strip()[-4000:])
    return ran.stdout.strip()


def compile_whole_model(engine: Path) -> str:
    """Type-check the model's whole file, not the slices of it.

    A slice compiles the functions alone against a state the test writes, so it cannot see a struct that
    moved above its own definition -- which is exactly the error that reached CI, after the local slices
    passed. This compiles the file itself against the declarations in stub/, so anything the model starts
    using has to be complete and in order.
    """
    source = engine / "hw/arm/s5l8930_isp.c"
    command = [shutil.which("cc") or "cc", "-fsyntax-only", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter",
               "-I", str(HERE / "stub"), "-I", str(engine / "hw/arm"), "-I", str(engine / "include"),
               str(source)]
    built = sh(command)
    if built.returncode:
        raise Failure("the model does not compile:\n" + built.stderr.strip()[-4000:])
    return f"{source.name}: {len(source.read_text(errors='ignore').splitlines())} lines"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", help="engine checkout (default: $LT_ENGINE_SOURCE or ../qemu-ios)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the generated sources")
    parser.add_argument("--sanitize", action="store_true",
                        help="build the slice tests with -fsanitize=address,undefined")
    options = parser.parse_args()
    report = Report(options.verbose)
    scratch = Path(tempfile.mkdtemp(prefix="lt-camera-"))

    global ENGINE, SANITIZE
    SANITIZE = options.sanitize
    try:
        ENGINE, engine_note = resolve_engine(options.engine, scratch)
        print(f"engine: {engine_note}")

        print("the model, compiled whole")
        if ENGINE is None:
            report.add("isp model", True, "skipped: no engine checkout")
        else:
            report.run("isp model: hw/arm/s5l8930_isp.c compiles as a whole", lambda: compile_whole_model(ENGINE))

        print("device ISP frame")
        if ENGINE is None:
            report.add("isp frame", True, "skipped: no engine checkout")
        else:
            report.run("isp frame: the newest frame reaches the guest, both sensors apart", lambda: compile_and_run(
                report, "isp frame", HERE / "isp-frame.c", shutil.which("cc") or "cc",
                ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"],
                [ENGINE / "hw/arm/host-camera.c"],
                [HERE / "stub", ENGINE / "include", ROOT], scratch))

        print("device ISP mailbox")
        if ENGINE is None:
            report.add("isp poll", True, "skipped: no engine checkout")
        else:
            report.run("isp poll: a register nothing can move stops being polled for ever", lambda: compile_and_run(
                report, "isp poll", HERE / "isp-poll.c", shutil.which("cc") or "cc",
                ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"], [], [HERE / "stub"], scratch))

        print("device ISP registers")
        if ENGINE is None:
            report.add("isp registers", True, "skipped: no engine checkout")
        else:
            report.run("isp registers: a poll of hardware that is not modelled ends", lambda: compile_and_run(
                report, "isp spin", HERE / "isp-spin.c", shutil.which("cc") or "cc",
                ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"], [], [HERE / "stub"], scratch))

        print("portable and JNI glue")
        report.run("portable API: NV12 shape, sensors, LT_AGAIN, LT_UNSUPPORTED", lambda: compile_and_run(
            report, "portable api", HERE / "portable-api.cpp", (shutil.which("c++") or "c++"),
            ["-Wall", "-Werror"], [], [ROOT, ROOT / "Portable/include", HERE / "stub"], scratch))
        report.run("JNI glue: planar and semi-planar frames, counters, return codes", lambda: compile_and_run(
            report, "jni glue", HERE / "jni-glue.cpp", shutil.which("c++") or "c++",
            ["-Wall", "-Werror"], [], [ROOT, ROOT / "Portable/include"], scratch))

        print("wiring between the layers")
        for name, check in wiring.CHECKS:
            report.run(name, check)

        print("engine patches")
        report.run("patches: stack applies and commits to a clean tree",
                   lambda: wiring.patch_stack(PATCHES, options.engine or ENGINE))
    finally:
        if options.keep:
            print(f"generated sources kept in {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    failed = report.failed
    print()
    if failed:
        print(f"{len(failed)} of {len(report.rows)} checks failed:")
        for name, _, detail in failed:
            print(f"  {name}: {detail.splitlines()[0] if detail else ''}")
        return 1
    print(f"all {len(report.rows)} camera checks pass")
    return 0


ENGINE = None
SANITIZE = False

if __name__ == "__main__":
    sys.exit(main())
