import tempfile
import unittest
from pathlib import Path

from rtve_dl.tmp_layout import TmpLayout
from rtve_dl.workflows.download import SeriesPaths, _collect_local_subs_for_mux


def _write_srt(path: Path) -> None:
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\nhola\n\n", encoding="utf-8")


class CollectLocalSubsTests(unittest.TestCase):
    def _paths(self) -> tuple[SeriesPaths, tempfile.TemporaryDirectory]:
        tmpdir = tempfile.TemporaryDirectory()
        root = Path(tmpdir.name)
        tmp_root = root / "tmp"
        out_root = root / "data"
        layout = TmpLayout.for_slug(tmp_root)
        layout.ensure_dirs()
        out_root.mkdir(parents=True, exist_ok=True)
        return SeriesPaths(slug="test", out=out_root, tmp=tmp_root, layout=layout), tmpdir

    def test_collect_subs_alignment_off_uses_raw(self) -> None:
        paths, root = self._paths()
        try:
            srt_es = paths.layout.srt_es_file("S01E01_test")
            _write_srt(srt_es)
            result = _collect_local_subs_for_mux(
                base="S01E01_test",
                paths=paths,
                with_ru=False,
                translate_en_if_missing=False,
                subtitle_align="off",
            )
            self.assertIsNotNone(result)
            subs, _default = result
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0][0], srt_es)
        finally:
            root.cleanup()

    def test_collect_subs_alignment_whisperx_uses_aligned(self) -> None:
        paths, root = self._paths()
        try:
            srt_es = paths.layout.srt_es_file("S01E01_test")
            srt_es_aligned = paths.layout.srt_es_aligned_file("S01E01_test")
            _write_srt(srt_es)
            _write_srt(srt_es_aligned)
            result = _collect_local_subs_for_mux(
                base="S01E01_test",
                paths=paths,
                with_ru=False,
                translate_en_if_missing=False,
                subtitle_align="whisperx",
            )
            self.assertIsNotNone(result)
            subs, _default = result
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0][0], srt_es_aligned)
        finally:
            root.cleanup()

    def test_collect_subs_alignment_whisperx_missing_aligned(self) -> None:
        paths, root = self._paths()
        try:
            srt_es = paths.layout.srt_es_file("S01E01_test")
            _write_srt(srt_es)
            result = _collect_local_subs_for_mux(
                base="S01E01_test",
                paths=paths,
                with_ru=False,
                translate_en_if_missing=False,
                subtitle_align="whisperx",
            )
            self.assertIsNone(result)
        finally:
            root.cleanup()


if __name__ == "__main__":
    unittest.main()
