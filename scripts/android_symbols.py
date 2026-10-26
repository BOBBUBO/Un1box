#!/usr/bin/env python3
"""One C++ runtime per APK: a packaged library may only use libc++ through the packaged libc++_shared.so.

firmwarekit is cross-built with the Swift SDK for Android, which wants the newest NDK's libc++ (its runtime
calls std::__ndk1::__hash_memory, a symbol older NDKs do not have). The emulator's engine and frontend are
built with the workflow's NDK. One libc++_shared.so ships in the APK and serves all of them, so it must be
the one that satisfies every packaged library -- otherwise firmwarekit cannot start on the phone (its imports
are resolved when it is loaded, before it can print anything), or the process ends up with a second, static
copy of libc++ and crashes on the first allocation that crosses between them.

What is checked, per set of libraries that will live in one process:
  * every undefined C++ symbol is defined by a packaged library (missing symbol: fatal, the library cannot load);
  * no library other than libc++_shared.so defines libc++ symbols itself (duplicate runtime: reported).

    python3 scripts/android_symbols.py --readelf llvm-readelf FILE [FILE ...]
    python3 scripts/android_symbols.py --readelf llvm-readelf LIGHTTOUCH.apk   # every ABI in the APK
    python3 scripts/android_symbols.py --self-test

The APK check (scripts/check-android-package.py) runs the same functions on what was packaged.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# A dwarf-free column layout: "   12: 0000000000000ab0    24 FUNC    GLOBAL DEFAULT   13 name@@LIBC"
DYNSYM = re.compile(r"^\s*\d+:\s+[0-9a-fA-F]+\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
CPP_PREFIXES = ("_Z", "__cxa_", "_Unwind_")
SHARED_CXX = "libc++_shared.so"
# Every Android process gets these from libc (bionic), not from libc++.
SYSTEM_CXX_SYMBOLS = {"__cxa_finalize", "__cxa_atexit", "__cxa_thread_atexit_impl"}


def dynamic_symbols(path, readelf="readelf"):
    """(defined, undefined) dynamic symbols of one ELF, names without their version suffix."""
    result = subprocess.run(
        [readelf, "--dyn-syms", "-W", str(path)], text=True, capture_output=True, check=True
    )
    return parse_dynsyms(result.stdout)


def parse_dynsyms(text):
    """The same, from `readelf --dyn-syms -W` output."""
    defined, undefined = set(), set()
    for line in text.splitlines():
        match = DYNSYM.match(line)
        if not match:
            continue
        kind, bind, _visibility, index, name = match.groups()
        name = name.split("@")[0]
        if not name or kind in ("SECTION", "FILE", "NOTYPE") and name.startswith("$"):
            continue
        if index == "UND":
            # A weak undefined symbol is allowed to stay unresolved (it reads as null), so it cannot break a load.
            if bind != "WEAK":
                undefined.add(name)
        elif kind not in ("SECTION", "FILE"):
            defined.add(name)
    return defined, undefined


def cpp_symbols(names):
    return {name for name in names if name.startswith(CPP_PREFIXES) and name not in SYSTEM_CXX_SYMBOLS}


def check(libraries, readelf="readelf"):
    """libraries: {name as packaged: path}. Returns (missing, duplicates)."""
    symbols = {name: dynamic_symbols(path, readelf) for name, path in libraries.items()}
    provided = set()
    for defined, undefined in symbols.values():
        provided |= defined
    missing = {}
    for name, (_, undefined) in symbols.items():
        absent = sorted(cpp_symbols(undefined) - provided)
        if absent:
            missing[name] = absent
    shared = symbols.get(SHARED_CXX, (set(), set()))[0]
    duplicates = {}
    for name, (defined, _) in symbols.items():
        if name == SHARED_CXX:
            continue
        # A library that defines libc++ itself carries a second runtime beside the shared one.
        own = sorted(cpp_symbols(defined) & shared)
        if own:
            duplicates[name] = own
    return missing, duplicates


def report(libraries, readelf="readelf", stream=sys.stdout, system=None):
    """(ok, text lines): the check's verdict for one process's libraries."""
    missing, duplicates = check(libraries, readelf)
    lines = []
    for name, symbols in sorted(missing.items()):
        sample = ", ".join(symbols[:3])
        lines.append(
            f"FAIL {name}: {len(symbols)} C++ symbol(s) no packaged library defines ({sample}); "
            f"it cannot be loaded as packaged"
        )
    for name, symbols in sorted(duplicates.items()):
        sample = ", ".join(symbols[:3])
        lines.append(
            f"WARN {name}: defines {len(symbols)} libc++ symbol(s) itself ({sample}): a second runtime beside "
            f"{SHARED_CXX}"
        )
    absent = {} if system is None else unresolved(libraries, system, readelf)
    for name, symbols in sorted(absent.items()):
        sample = ", ".join(symbols[:12]) + (f", ... ({len(symbols)} in all)" if len(symbols) > 12 else "")
        lines.append(f"MISSING {name}: nothing in the package and nothing in the platform defines {sample}")
    if not lines:
        lines.append(f"ok   one C++ runtime across {len(libraries)} packaged libraries")
    print("\n".join(lines), file=stream)
    return not missing and not absent, lines


def annotate(lines):
    """A workflow annotation for GitHub Actions, escaped as it wants it, or nothing outside one."""
    findings = [line for line in lines if line.startswith(("MISSING", "FAIL", "WARN"))]
    if not findings or not os.environ.get("GITHUB_ACTIONS"):
        return
    level = "warning" if any(line.startswith(("MISSING", "WARN")) for line in findings) else "error"
    text = "\n".join(findings)
    print(f"::{level} title=native symbols::{text}".replace("%", "%25").replace("\n", "%0A"),
          file=sys.stderr)


def self_test():
    """Compile two throwaway libraries and check that the mismatch is seen and the match is not."""
    import shutil

    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        print("skip: no C++ compiler to build the fixtures with", file=sys.stderr)
        return 0
    with tempfile.TemporaryDirectory(prefix="android-symbols-") as temporary:
        directory = Path(temporary)
        (directory / "provider.cpp").write_text("namespace std { int __hash_memory() { return 7; } }\n"
                                                "int provider() { return std::__hash_memory(); }\n")
        (directory / "consumer.cpp").write_text("namespace std { int __hash_memory(); }\n"
                                                "int consumer() { return std::__hash_memory(); }\n")
        subprocess.run([compiler, "-shared", "-fPIC", "-o", str(directory / "libc++_shared.so"),
                        str(directory / "provider.cpp")], check=True)
        subprocess.run([compiler, "-shared", "-fPIC", "-o", str(directory / "libuser.so"),
                        str(directory / "consumer.cpp")], check=True)
        readelf = shutil.which("llvm-readelf") or shutil.which("readelf")
        if readelf is None:
            print("skip: no readelf to read the fixtures with", file=sys.stderr)
            return 0
        providers = {SHARED_CXX: directory / "libc++_shared.so"}
        absent, _ = check({"libuser.so": directory / "libuser.so"}, readelf)
        present, _ = check(dict(providers, **{"libuser.so": directory / "libuser.so"}), readelf)
        good = "libuser.so" in absent and not present
        print(("ok   " if good else "FAIL ") + "the self-test sees a missing C++ symbol and accepts a provided one")
        return 0 if good else 1


def system_definitions(directory, readelf="readelf"):
    """Every symbol the platform provides: the NDK's stub libraries for one API level."""
    defined = set()
    for path in sorted(Path(directory).glob("*.so")):
        try:
            part, _ = dynamic_symbols(path, readelf)
        except (subprocess.CalledProcessError, OSError):
            continue
        defined |= part
    return defined


def unresolved(libraries, system, readelf="readelf"):
    """{library: symbols} for imports that neither a packaged library nor the platform defines.

    A function imported and never resolved is not an error at load time with lazy binding: the call jumps to
    address zero and the process dies of SIGSEGV without a word. Checks the C++ symbols above cannot see it,
    because it is not a C++ symbol at all.
    """
    symbols = {name: dynamic_symbols(path, readelf) for name, path in libraries.items()}
    provided = set(system)
    for defined, _ in symbols.values():
        provided |= defined
    missing = {}
    for name, (_, undefined) in symbols.items():
        absent = sorted(undefined - provided)
        if absent:
            missing[name] = absent
    return missing


def apk_libraries(apk):
    """{abi: {name: path}} for the native libraries in an APK, unpacked into a temporary directory."""
    import zipfile

    unpacked = tempfile.mkdtemp(prefix="android-symbols-")
    libraries = {}
    with zipfile.ZipFile(apk) as archive:
        for member in archive.infolist():
            parts = member.filename.split("/")
            if len(parts) != 3 or parts[0] != "lib" or not parts[2].endswith(".so"):
                continue
            path = Path(unpacked) / parts[1] / parts[2]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))
            libraries.setdefault(parts[1], {})[parts[2]] = path
    return libraries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("libraries", nargs="*", type=Path,
                        help="packaged libraries, one process's worth (the file name is its packaged name)")
    parser.add_argument("--readelf", default="readelf")
    parser.add_argument("--system-libs", type=Path,
                        help="a directory of the platform's stub libraries (the NDK's sysroot for one API): also "
                             "report every import that neither the package nor the platform defines, since such a "
                             "call jumps to address zero and the process dies of SIGSEGV without a message")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    system = None if args.system_libs is None else system_definitions(args.system_libs, args.readelf)
    if args.self_test:
        return self_test()
    if not args.libraries:
        parser.error("name the libraries that ship together, e.g. libc++_shared.so libfirmwarekit.so")
    if len(args.libraries) == 1 and args.libraries[0].suffix.lower() in (".apk", ".zip"):
        by_abi = apk_libraries(args.libraries[0])
        if not by_abi:
            print(f"FAIL {args.libraries[0]}: it holds no native libraries", file=sys.stderr)
            return 1
        verdicts = []
        for abi in sorted(by_abi):
            print(f"{abi}:")
            ok, lines = report(by_abi[abi], args.readelf, system=system)
            annotate([f"{abi}/{line}" for line in lines])
            verdicts.append(ok)
        return 0 if all(verdicts) else 1
    ok, lines = report({path.name: path for path in args.libraries}, args.readelf, system=system)
    annotate(lines)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
