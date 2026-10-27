"""Synthetic PE tables exercise inventory without executing native binaries."""
import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('pe_inventory', Path(__file__).resolve().parents[2] / 'Windows/tools/pe_inventory.py')
pe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pe)


def image(machine=0x8664, imports=('KERNEL32.dll', 'python313.dll')):
    data = bytearray(0x600)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, machine, 1)
    optional, size = 0x98, 0xe0 if machine == 0x14c else 0xf0
    struct.pack_into('<H', data, 0x94, size)
    struct.pack_into('<H', data, optional, 0x10b if machine == 0x14c else 0x20b)
    struct.pack_into('<I', data, optional + 60, 0x200)
    directory = 96 if machine == 0x14c else 112
    struct.pack_into('<I', data, optional + directory - 4, 16)
    struct.pack_into('<II', data, optional + directory + 8, 0x1000 if imports else 0, (len(imports) + 1) * 20 if imports else 0)
    struct.pack_into('<IIII', data, optional + size + 8, 0x400, 0x1000, 0x400, 0x200)
    cursor = 0x300
    for index, name in enumerate(imports):
        struct.pack_into('<IIIII', data, 0x200 + index * 20, 0, 0, 0, cursor - 0x200 + 0x1000, 1)
        encoded = name.encode('ascii') + b'\0'
        data[cursor:cursor + len(encoded)] = encoded
        cursor += len(encoded)
    return data


class PeTests(unittest.TestCase):
    def test_x64_runtime_import_names_resolve_section_rvas(self):
        result = pe.inspect_pe(image(), 'DLLs/_tkinter.pyd')
        self.assertEqual(result, {'machine': '0x8664', 'auxiliary_launcher_data': False,
                                  'imports': ['KERNEL32.dll', 'python313.dll']})

    def test_only_exact_x86_and_arm64_auxiliary_paths_are_accepted(self):
        for name, machine in pe.AUXILIARY_LAUNCHERS.items():
            with self.subTest(name=name):
                result = pe.inspect_pe(image(machine), name)
                self.assertTrue(result['auxiliary_launcher_data'])
                self.assertEqual(result['machine'], hex(machine))
                self.assertEqual(result['imports'], ['KERNEL32.dll', 'python313.dll'])
                with self.assertRaises(ValueError):
                    pe.inspect_pe(image(machine), 'DLLs/' + Path(name).name)
                with self.assertRaises(ValueError):
                    pe.inspect_pe(image(), name)

    def test_no_import_directory(self):
        self.assertEqual(pe.inspect_pe(image(imports=()), 'python.exe')['imports'], [])

    def test_truncated_headers_and_sections(self):
        for data in (b'MZ', image()[:0x100], image()[:0x500]):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                pe.inspect_pe(data, 'python.exe')

    def test_import_rva_outside_sections_is_rejected(self):
        data = image()
        struct.pack_into('<I', data, 0x98 + 120, 0x9000)
        with self.assertRaisesRegex(ValueError, 'RVA'):
            pe.inspect_pe(data, 'python.exe')

    def test_import_descriptor_and_name_terminators_are_required(self):
        data = image()
        struct.pack_into('<I', data, 0x98 + 124, 40)
        with self.assertRaisesRegex(ValueError, 'terminator'):
            pe.inspect_pe(data, 'python.exe')
        data = image()
        data[0x300:0x600] = b'X' * 0x300
        with self.assertRaisesRegex(ValueError, 'unterminated'):
            pe.inspect_pe(data, 'python.exe')

    def test_wrong_optional_header_architecture_and_path_names_rejected(self):
        data = image()
        struct.pack_into('<H', data, 0x98, 0x10b)
        with self.assertRaisesRegex(ValueError, 'format'):
            pe.inspect_pe(data, 'python.exe')
        with self.assertRaisesRegex(ValueError, 'DLL name'):
            pe.inspect_pe(image(imports=('../bad.dll',)), 'python.exe')


if __name__ == '__main__':
    unittest.main()
