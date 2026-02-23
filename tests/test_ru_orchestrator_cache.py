import tempfile
import unittest
from pathlib import Path

from rtve_dl.subtitle_tracks.orchestrator import build_ru_tracks
from rtve_dl.tmp_layout import TmpLayout


def _write_srt(path: Path) -> None:
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\nпривет\n\n", encoding="utf-8")


class _Cue:
    def __init__(self, start_ms: int, end_ms: int, text: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.text = text


class _FailCache:
    def split_for_track(self, *, cues, track):  # noqa: ANN001
        raise AssertionError(f"split_for_track should not be called when RU SRT is cached (track={track})")


class RuOrchestratorCacheTests(unittest.TestCase):
    def test_ru_cached_srt_skips_translation(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        try:
            root = Path(tmpdir.name)
            layout = TmpLayout.for_slug(root / "tmp" / "test")
            layout.ensure_dirs()
            base = "S01E01_test"
            ru_srt = layout.srt_ru_file(base)
            _write_srt(ru_srt)

            tracks = build_ru_tracks(
                cues=[_Cue(0, 1000, "hola")],
                base=base,
                asset_id="asset-1",
                layout=layout,
                global_cache=_FailCache(),  # type: ignore[arg-type]
                primary_model="sonnet",
                fallback_model=None,
                codex_chunk_cues=500,
                jobs_codex_chunks=1,
                translation_backend="claude",
                no_chunk=False,
                telemetry=None,
                run_id=0,
                enabled_track_ids={"ru"},
                force_asr=False,
            )
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].path, ru_srt)
        finally:
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
