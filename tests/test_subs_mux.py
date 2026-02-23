import tempfile
import unittest
from pathlib import Path

from rtve_dl.tmp_layout import TmpLayout
from rtve_dl.subtitle_tracks.policy import enabled_ru_track_ids, parse_track_policy
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
                policy=parse_track_policy(["es=on", "en=off", "ru=off", "ru-dual=off", "refs=off"]),
                enabled_ru_tracks=set(),
                default_subtitle="es",
                subtitle_align="off",
            )
            self.assertIsNotNone(result)
            subs, _default = result
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0].path, srt_es)
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
                policy=parse_track_policy(["es=on", "en=off", "ru=off", "ru-dual=off", "refs=off"]),
                enabled_ru_tracks=set(),
                default_subtitle="es",
                subtitle_align="whisperx",
            )
            self.assertIsNotNone(result)
            subs, _default = result
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0].path, srt_es_aligned)
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
                policy=parse_track_policy(["es=on", "en=off", "ru=off", "ru-dual=off", "refs=off"]),
                enabled_ru_tracks=set(),
                default_subtitle="es",
                subtitle_align="whisperx",
            )
            self.assertIsNone(result)
        finally:
            root.cleanup()

    def test_collect_subs_ru_refs_off_requires_ru_dual_only(self) -> None:
        paths, root = self._paths()
        try:
            base = "S01E01_test"
            _write_srt(paths.layout.srt_es_file(base))
            _write_srt(paths.layout.srt_en_file(base))
            _write_srt(paths.layout.srt_ru_file(base))
            _write_srt(paths.layout.srt_bi_full_file(base))
            policy = parse_track_policy(["ru=on", "refs=off", "ru-dual=on", "en=on", "es=on"])
            enabled = enabled_ru_track_ids(policy=policy, force_asr=False)
            result = _collect_local_subs_for_mux(
                base=base,
                paths=paths,
                policy=policy,
                enabled_ru_tracks=enabled,
                default_subtitle="ru-dual",
                subtitle_align="off",
            )
            self.assertIsNotNone(result)
            subs, default_title = result
            self.assertEqual(default_title, "ES+RU")
            self.assertFalse(any(t.id == "refs" for t in subs))
        finally:
            root.cleanup()

    def test_collect_subs_fails_when_default_missing(self) -> None:
        paths, root = self._paths()
        try:
            base = "S01E01_test"
            _write_srt(paths.layout.srt_es_file(base))
            with self.assertRaises(RuntimeError):
                _collect_local_subs_for_mux(
                    base=base,
                    paths=paths,
                    policy=parse_track_policy(["es=on", "en=off", "ru=off", "ru-dual=off", "refs=off"]),
                    enabled_ru_tracks=set(),
                    default_subtitle="refs",
                    subtitle_align="off",
                )
        finally:
            root.cleanup()

    def test_collect_subs_es_off_allows_no_es_track(self) -> None:
        paths, root = self._paths()
        try:
            base = "S01E01_test"
            policy = parse_track_policy(["es=off", "en=off", "ru=off", "ru-dual=off", "refs=off"])
            with self.assertRaises(RuntimeError):
                _collect_local_subs_for_mux(
                    base=base,
                    paths=paths,
                    policy=policy,
                    enabled_ru_tracks=set(),
                    default_subtitle="es",
                    subtitle_align="off",
                )
        finally:
            root.cleanup()


if __name__ == "__main__":
    unittest.main()
