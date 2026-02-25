import tempfile
import unittest
from pathlib import Path

from rtve_dl.subtitle_tracks.models import TRACK_REFS, TRACK_REFS_ASR, TRACK_RU, TRACK_RU_ASR, TRACK_RU_DUAL, TRACK_RU_DUAL_ASR
from rtve_dl.subtitle_tracks.orchestrator import track_file_specs
from rtve_dl.tmp_layout import TmpLayout


class TrackLanguageTagTests(unittest.TestCase):
    def test_normal_mode_language_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = TmpLayout.for_slug(Path(tmp))
            specs = track_file_specs(layout=layout, base="S01E01_test", force_asr=False, primary_model="sonnet")
            self.assertEqual(specs[TRACK_RU].lang, "rus")
            self.assertEqual(specs[TRACK_REFS].lang, "und")
            self.assertEqual(specs[TRACK_RU_DUAL].lang, "mul")

    def test_force_asr_mode_language_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = TmpLayout.for_slug(Path(tmp))
            specs = track_file_specs(layout=layout, base="S01E01_test", force_asr=True, primary_model="sonnet")
            self.assertEqual(specs[TRACK_RU_ASR].lang, "rus")
            self.assertEqual(specs[TRACK_REFS_ASR].lang, "und")
            self.assertEqual(specs[TRACK_RU_DUAL_ASR].lang, "mul")


if __name__ == "__main__":
    unittest.main()
