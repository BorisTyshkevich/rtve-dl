import unittest

from rtve_dl.subs.sync_compare import parse_rust_inspector_output, summarize_shift_deltas
from rtve_dl.subs.vtt import Cue


class SyncCompareTests(unittest.TestCase):
    def test_summarize_shift_deltas_reports_uniform_shift(self) -> None:
        original = [
            Cue(start_ms=1000, end_ms=2000, text="hola"),
            Cue(start_ms=2500, end_ms=3200, text="adios"),
        ]
        shifted = [
            Cue(start_ms=1280, end_ms=2280, text="hola"),
            Cue(start_ms=2780, end_ms=3480, text="adios"),
        ]
        summary = summarize_shift_deltas(original, shifted)
        self.assertEqual(summary.count, 2)
        self.assertEqual(summary.median_ms, 280)
        self.assertEqual(summary.min_ms, 280)
        self.assertEqual(summary.max_ms, 280)
        self.assertEqual(summary.unique_count, 1)
        self.assertEqual(summary.unique_sample_ms, (280,))

    def test_parse_rust_inspector_output_extracts_final_estimate(self) -> None:
        stdout = "\n".join(
            [
                "mp4: /tmp/demo.mp4",
                "final: delay_ms=481 confidence=0.498",
            ]
        )
        estimate = parse_rust_inspector_output(stdout)
        assert estimate is not None
        self.assertEqual(estimate.delay_ms, 481)
        self.assertAlmostEqual(estimate.confidence, 0.498)


if __name__ == "__main__":
    unittest.main()
