"""Regression checks for the packaged ELF contract, without proprietary inputs."""
import importlib.util
from pathlib import Path
import struct
import unittest

SPEC = importlib.util.spec_from_file_location(
    "package_check", Path(__file__).resolve().parents[2] / "scripts/check-android-package.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def elf(machine=183, alignment=16384, flags=5, address=0):
    result = bytearray(128)
    result[:16] = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<HHIQQQIHHHHHH", result, 16,
                     3, machine, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", result, 64, 1, flags, 0, address, address, 128, 128, alignment)
    return result


class NativePackageContract(unittest.TestCase):
    def test_accepts_both_target_machines(self):
        for abi, machine in CHECK.MACHINES.items():
            self.assertEqual(CHECK.inspect_elf(elf(machine), abi)[0]["alignment"], 16384)

    def test_rejects_4k_dependency_even_when_other_libraries_are_aligned(self):
        with self.assertRaisesRegex(ValueError, "alignment"):
            CHECK.inspect_elf(elf(alignment=4096), "arm64-v8a")

    def test_rejects_abi_mismatch(self):
        with self.assertRaisesRegex(ValueError, "machine"):
            CHECK.inspect_elf(elf(machine=62), "arm64-v8a")

    def test_rejects_incongruent_segment_offsets(self):
        with self.assertRaisesRegex(ValueError, "incongruent"):
            CHECK.inspect_elf(elf(address=4096), "arm64-v8a")

    def test_rejects_writable_executable_segments(self):
        with self.assertRaisesRegex(ValueError, "writable executable"):
            CHECK.inspect_elf(elf(flags=7), "arm64-v8a")

    def test_rejects_truncated_segment(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            CHECK.inspect_elf(elf()[:-1], "arm64-v8a")


class CppRuntimeContract(unittest.TestCase):
    """The APK ships one libc++_shared.so: every other library must be able to load against it."""

    READELF = """Symbol table '.dynsym' contains 3 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000000     0 NOTYPE  GLOBAL DEFAULT  UND _ZNSt6__ndk112__hash_memoryEPKvm
     2: 0000000000001109    11 FUNC    GLOBAL DEFAULT   10 _Z8consumerv
"""

    def test_parses_readelf_dynamic_symbols(self):
        defined, undefined = CHECK.android_symbols.parse_dynsyms(self.READELF)
        self.assertEqual(defined, {"_Z8consumerv"})
        self.assertEqual(undefined, {"_ZNSt6__ndk112__hash_memoryEPKvm"})

    def test_names_a_symbol_no_packaged_library_defines(self):
        check = CHECK.android_symbols
        original = check.dynamic_symbols
        check.dynamic_symbols = lambda path, readelf="readelf": check.parse_dynsyms(self.READELF)
        try:
            missing, duplicates = check.check({"libfirmwarekit.so": Path("libfirmwarekit.so")}, "readelf")
        finally:
            check.dynamic_symbols = original
        self.assertEqual(missing, {"libfirmwarekit.so": ["_ZNSt6__ndk112__hash_memoryEPKvm"]})
        self.assertEqual(duplicates, {})

    def test_accepts_the_same_symbol_when_libcxx_defines_it(self):
        check = CHECK.android_symbols
        provider = """Symbol table '.dynsym' contains 2 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000ab0    24 FUNC    GLOBAL DEFAULT   13 _ZNSt6__ndk112__hash_memoryEPKvm
"""

        def symbols(path, readelf="readelf"):
            return check.parse_dynsyms(provider if str(path).endswith("libc++_shared.so") else self.READELF)

        original = check.dynamic_symbols
        check.dynamic_symbols = symbols
        try:
            missing, duplicates = check.check(
                {"libc++_shared.so": Path("libc++_shared.so"), "libfirmwarekit.so": Path("libfirmwarekit.so")},
                "readelf",
            )
        finally:
            check.dynamic_symbols = original
        self.assertEqual(missing, {})
        self.assertEqual(duplicates, {})

    def test_ignores_bionic_cxa_symbols(self):
        self.assertEqual(CHECK.android_symbols.cpp_symbols({"__cxa_finalize", "__cxa_atexit", "_Z3foov"}),
                         {"_Z3foov"})


if __name__ == "__main__":
    unittest.main()
