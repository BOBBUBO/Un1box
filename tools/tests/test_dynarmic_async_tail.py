import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('blocks',Path(__file__).resolve().parents[1]/'arm1176-dynarmic-blocks.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
def record(n):
    return f'LT_DYNARMIC_BLOCK run={n} count=2 pc=0x00001a00 next=0x00001a00 r0={n} reused=1 code_reads=0 data_reads=0 jit=1 generation=1\n'.encode()

class TailTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'engine.log'
    def test_resumed_phase_ignores_large_prior_prefix_but_final_parser_retains_it(self):
        prefix=b''.join(record(n) for n in range(1,10001));self.path.write_bytes(prefix+record(10001)+record(10002))
        self.assertEqual([v['run'] for v in b.witnesses(self.path,len(prefix))],[10001,10002])
        self.assertEqual(len(b.witnesses(self.path)),10002)
    def test_incomplete_live_record_never_counts(self):
        prefix=record(1);self.path.write_bytes(prefix+record(2)+record(3)[:-1])
        self.assertEqual([v['run'] for v in b.witnesses(self.path,len(prefix))],[2])
    def test_whole_log_validation_still_rejects_malformed_earlier_record(self):
        prefix=b'LT_DYNARMIC_BLOCK malformed\n';self.path.write_bytes(prefix+record(2))
        self.assertEqual(b.witnesses(self.path,len(prefix))[0]['run'],2)
        with self.assertRaisesRegex(RuntimeError,'Malformed'):b.witnesses(self.path)
    def test_nonboundary_and_truncated_offsets_refused(self):
        self.path.write_bytes(record(1))
        for offset in [3,self.path.stat().st_size+1]:
            with self.subTest(offset=offset),self.assertRaisesRegex(RuntimeError,'boundary'):b.witnesses(self.path,offset)

if __name__=='__main__':unittest.main()
