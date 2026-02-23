import unittest
from pathlib import Path

from rtve_dl.subtitle_tracks.defaults import resolve_default_subtitle_title
from rtve_dl.subtitle_tracks.models import ProducedTrack, TRACK_REFS, TRACK_RU_DUAL


class DefaultSubtitleSelectionTests(unittest.TestCase):
    def test_resolve_refs(self) -> None:
        subs = [
            ProducedTrack(TRACK_RU_DUAL, Path("a.srt"), "rus", "ES+RU"),
            ProducedTrack(TRACK_REFS, Path("b.srt"), "spa", "ES+RU refs"),
        ]
        self.assertEqual(resolve_default_subtitle_title(subs, "refs"), "ES+RU refs")

    def test_resolve_missing_hard_fails(self) -> None:
        subs = [ProducedTrack(TRACK_RU_DUAL, Path("a.srt"), "rus", "ES+RU")]
        with self.assertRaises(RuntimeError):
            resolve_default_subtitle_title(subs, "refs")


if __name__ == "__main__":
    unittest.main()
