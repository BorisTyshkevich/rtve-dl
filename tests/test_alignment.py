import unittest

from rtve_dl.subs.align_whisperx import retime_cues_from_segments
from rtve_dl.subs.vtt import Cue


class AlignmentRetimingTests(unittest.TestCase):
    def test_retime_updates_boundaries(self) -> None:
        cues = [
            Cue(start_ms=1000, end_ms=2000, text="hola"),
            Cue(start_ms=3000, end_ms=4000, text="mundo"),
        ]
        segments = [
            {"words": [{"start": 1.2, "end": 1.8}]},
            {"words": [{"start": 3.1, "end": 3.7}]},
        ]
        out = retime_cues_from_segments(cues, segments)
        self.assertEqual(out[0].start_ms, 1200)
        self.assertEqual(out[0].end_ms, 1800)
        self.assertEqual(out[1].start_ms, 3100)
        self.assertEqual(out[1].end_ms, 3700)

    def test_retime_missing_words_fallback(self) -> None:
        cues = [Cue(start_ms=1000, end_ms=2000, text="hola")]
        segments = [{"words": []}]
        out = retime_cues_from_segments(cues, segments)
        self.assertEqual(out[0].start_ms, 1000)
        self.assertEqual(out[0].end_ms, 2000)

    def test_retime_non_monotonic_words(self) -> None:
        cues = [Cue(start_ms=1000, end_ms=2000, text="hola")]
        segments = [{"words": [{"start": 1.9, "end": 2.0}, {"start": 1.1, "end": 1.2}]}]
        out = retime_cues_from_segments(cues, segments)
        self.assertEqual(out[0].start_ms, 1100)
        self.assertEqual(out[0].end_ms, 2000)

    def test_retime_invalid_word_times(self) -> None:
        cues = [Cue(start_ms=1000, end_ms=2000, text="hola")]
        segments = [{"words": [{"start": 1.5}]}]
        out = retime_cues_from_segments(cues, segments)
        self.assertEqual(out[0].start_ms, 1000)
        self.assertEqual(out[0].end_ms, 2000)


if __name__ == "__main__":
    unittest.main()
