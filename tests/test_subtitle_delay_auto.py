import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rtve_dl.constants import DEFAULT_SUBTITLE_DELAY_MS
from rtve_dl.rtve.catalog import SeriesAsset
from rtve_dl.subs.delay_auto import (
    AUTO_DELAY_ASR_SEGMENT_S,
    AUTO_DELAY_PRIMARY_START_S,
    DelayEstimate,
    _auto_delay_clip_starts,
    _select_delay_cluster,
    estimate_series_delay_ms,
)


class SubtitleDelayAutoTests(unittest.TestCase):
    def test_select_delay_cluster_prefers_consistent_offset_over_noise(self) -> None:
        matches = [
            (-80, 4.0),
            (-60, 4.0),
            (-40, 3.5),
            (0, 3.0),
            (20, 2.5),
            (510, 16.0),
            (525, 18.0),
            (540, 19.0),
            (550, 24.0),
            (560, 20.0),
            (575, 18.0),
            (590, 16.0),
            (605, 13.0),
            (620, 12.0),
            (635, 11.0),
            (650, 10.0),
            (665, 9.0),
        ]

        selected = _select_delay_cluster(matches)

        self.assertIsNotNone(selected)
        delay_ms, matched, cluster_ratio = selected
        self.assertEqual(delay_ms, 560)
        self.assertEqual(matched, 12)
        self.assertGreater(cluster_ratio, 0.8)

    def test_estimate_series_delay_discards_low_confidence_energy_when_asr_fails(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mp4_dir = root / "mp4"
            srt_dir = root / "srt"
            cache_dir = root / "cache"
            out_dir = root / "out"
            for path in (mp4_dir, srt_dir, cache_dir, out_dir):
                path.mkdir(parents=True, exist_ok=True)

            asset = SeriesAsset(
                asset_id="123",
                episode_url="https://example.invalid/episode",
                title="Test Episode",
                short_description=None,
                description=None,
                season=1,
                episode=1,
                has_drm=False,
            )
            base = "S01E01_test_episode"
            (mp4_dir / f"{base}.mp4").write_bytes(b"mp4")
            (srt_dir / f"{base}.spa.srt").write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhola\n\n",
                encoding="utf-8",
            )

            with (
                patch(
                    "rtve_dl.subs.delay_auto._estimate_by_energy",
                    return_value=DelayEstimate(delay_ms=14800, confidence=0.0, method="energy", matched=10),
                ),
                patch("rtve_dl.subs.delay_auto._estimate_by_asr", return_value=None),
            ):
                delay_ms = estimate_series_delay_ms(
                    assets=[asset],
                    mp4_dir=mp4_dir,
                    srt_dir=srt_dir,
                    cache_dir=cache_dir,
                    out_dir=out_dir,
                    scope="episode",
                    samples=1,
                    max_ms=15000,
                    asr_backend="whisperx",
                    asr_model="small",
                    asr_device="cpu",
                    asr_compute_type="int8",
                    asr_batch_size=8,
                    asr_vad_method="silero",
                    asr_mlx_model="mlx-community/whisper-small-mlx",
                )

            self.assertEqual(delay_ms, DEFAULT_SUBTITLE_DELAY_MS)

    def test_auto_delay_clip_starts_prefers_60s_then_middle(self) -> None:
        starts = _auto_delay_clip_starts(4059.04)

        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], AUTO_DELAY_PRIMARY_START_S)
        self.assertAlmostEqual(starts[1], (4059.04 / 2.0) - (AUTO_DELAY_ASR_SEGMENT_S / 2.0))

    def test_auto_delay_clip_starts_uses_full_file_for_short_media(self) -> None:
        self.assertEqual(_auto_delay_clip_starts(240.0), [None])


if __name__ == "__main__":
    unittest.main()
