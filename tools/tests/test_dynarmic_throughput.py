"""Host-only evidence negatives; no compiler, QEMU or guest process."""
import copy
import importlib.util
import pathlib
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('throughput',
    pathlib.Path(__file__).resolve().parents[1]/'arm1176-dynarmic-throughput.py')
throughput=importlib.util.module_from_spec(spec)
spec.loader.exec_module(throughput)


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(blocks=10,instructions=40,cold=1,warm=9,
                       first_us=10,cold_end_us=20,last_us=30,
                       pc=0x1c00,r0=10,r1=65526,cpsr=0x20000010),
                   dict(blocks=65526,instructions=262104,cold=1,warm=65525,
                       first_us=31,cold_end_us=40,last_us=50,
                       pc=0x1c10,r0=65536,r1=0,cpsr=0x60000010)]

    def test_split_at_conservative_exit_retains_exact_coverage(self):
        totals=throughput.validate_aggregates(self.rows,65536)
        self.assertEqual(totals,dict(segments=2,blocks=65536,
                         instructions=262144,cold=2,warm=65534))

    def test_missing_duplicate_or_tcg_coverage_rejected(self):
        for rows in [[],self.rows[:1],self.rows+self.rows[-1:]]:
            with self.subTest(rows=len(rows)),self.assertRaises(RuntimeError):
                throughput.validate_aggregates(rows,65536)

    def test_corrupted_boundary_state_count_and_time_rejected(self):
        for field in ['blocks','instructions','cold','warm','pc','r0','r1','cpsr',
                      'first_us','cold_end_us','last_us']:
            rows=copy.deepcopy(self.rows)
            rows[1][field]=1 if rows[1][field]==0 else 0
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                throughput.validate_aggregates(rows,65536)

    def test_malformed_native_witness_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=pathlib.Path(temporary)/'log'
            path.write_text('LT_DYNARMIC_THROUGHPUT blocks=1 truncated\n')
            with self.assertRaises(RuntimeError):
                throughput.summaries(path)

    def test_proc_stat_identity_and_comm_with_spaces(self):
        fields=['S']+['0']*21
        fields[11]='17';fields[12]='4';fields[19]='12345'
        with tempfile.TemporaryDirectory() as temporary:
            path=pathlib.Path(temporary)/'stat'
            path.write_text('42 (name with ) embedded spaces) '+' '.join(fields))
            actual=throughput.process_stat(path)
        self.assertEqual((actual['state'],actual['utime_ticks'],
                         actual['stime_ticks'],actual['starttime_ticks']),
                        ('S',17,4,12345))

    def sample(self,ticks,iterations=65536):
        return dict(ticks_per_second=100,iterations=iterations,
                    status='FINITE_THROUGHPUT_STATE_OBSERVED',exit_code=0,
                    process_cpu_ticks=ticks,vcpu_cpu_ticks=ticks)

    def test_zero_tick_control_never_yields_speedup(self):
        for ticks in [0,1,2]:
            decision=throughput.cpu_benefit_decision(
                [self.sample(0) for _ in range(3)],
                [self.sample(ticks) for _ in range(3)])
            self.assertEqual(decision['decision'],'NO_BENEFIT_ESTABLISHED')

    def test_clear_total_cpu_loss_stops_candidate(self):
        decision=throughput.cpu_benefit_decision(
            [self.sample(0) for _ in range(3)],
            [self.sample(20) for _ in range(3)])
        self.assertEqual(decision['decision'],'STOP_CURRENT_CANDIDATE')

    def test_robust_twenty_percent_gain_only_advances_coverage(self):
        decision=throughput.cpu_benefit_decision(
            [self.sample(100) for _ in range(3)],
            [self.sample(60) for _ in range(3)])
        self.assertEqual(decision['decision'],'INVESTIGATE_COVERAGE')
        self.assertIn('never iOS',decision['scope'])

    def test_wrong_sample_count_and_cold_samples_rejected(self):
        with self.assertRaises(ValueError):
            throughput.cpu_benefit_decision([self.sample(100)],[self.sample(60)])
        with self.assertRaises(ValueError):
            throughput.cpu_benefit_decision([self.sample(100,1)]*3,
                                          [self.sample(60)]*3)


if __name__=='__main__':
    unittest.main()
