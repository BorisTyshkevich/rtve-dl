import unittest
from pathlib import Path
from unittest.mock import patch

from rtve_dl.ffmpeg import mux_mkv


class FfmpegMuxTests(unittest.TestCase):
    def test_copy_mode_keeps_stream_copy(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)

        with patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture):
            mux_mkv(
                video_path=Path("in.mp4"),
                out_mkv=Path("out.mkv"),
                subs=[(Path("a.srt"), "spa", "ES")],
                video_codec_mode="copy",
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("copy", calls[0])
        self.assertIn("-c:v", calls[0])

    def test_hevc_mode_defaults_to_cpu(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)

        with (
            patch("rtve_dl.ffmpeg._pick_available_hevc_gpu_encoder", return_value="hevc_videotoolbox"),
            patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture),
        ):
            mux_mkv(
                video_path=Path("in.mp4"),
                out_mkv=Path("out.mkv"),
                subs=[(Path("a.srt"), "spa", "ES")],
                video_codec_mode="hevc",
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("libx265", calls[0])

    def test_hevc_mode_uses_gpu_when_available(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)

        with (
            patch("rtve_dl.ffmpeg._pick_available_hevc_gpu_encoder", return_value="hevc_videotoolbox"),
            patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture),
        ):
            mux_mkv(
                video_path=Path("in.mp4"),
                out_mkv=Path("out.mkv"),
                subs=[(Path("a.srt"), "spa", "ES")],
                video_codec_mode="hevc",
                hevc_device="auto",
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("hevc_videotoolbox", calls[0])

    def test_hevc_mode_falls_back_to_cpu_when_gpu_fails(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("gpu failed")

        with (
            patch("rtve_dl.ffmpeg._pick_available_hevc_gpu_encoder", return_value="hevc_videotoolbox"),
            patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture),
        ):
            mux_mkv(
                video_path=Path("in.mp4"),
                out_mkv=Path("out.mkv"),
                subs=[(Path("a.srt"), "spa", "ES")],
                video_codec_mode="hevc",
                hevc_device="auto",
                hevc_crf=27,
                hevc_preset="slow",
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("hevc_videotoolbox", calls[0])
        self.assertIn("libx265", calls[1])
        self.assertIn("27", calls[1])
        self.assertIn("slow", calls[1])

    def test_hevc_mode_uses_cpu_when_no_gpu_encoder(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)

        with (
            patch("rtve_dl.ffmpeg._pick_available_hevc_gpu_encoder", return_value=None),
            patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture),
        ):
            mux_mkv(
                video_path=Path("in.mp4"),
                out_mkv=Path("out.mkv"),
                subs=[(Path("a.srt"), "spa", "ES")],
                video_codec_mode="hevc",
                hevc_device="auto",
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("libx265", calls[0])

    def test_hevc_mode_raises_when_both_gpu_and_cpu_fail(self) -> None:
        calls: list[list[str]] = []

        def _capture(args: list[str]) -> None:
            calls.append(args)
            raise RuntimeError("encode failed")

        with (
            patch("rtve_dl.ffmpeg._pick_available_hevc_gpu_encoder", return_value="hevc_videotoolbox"),
            patch("rtve_dl.ffmpeg.run_ffmpeg", side_effect=_capture),
        ):
            with self.assertRaises(RuntimeError):
                mux_mkv(
                    video_path=Path("in.mp4"),
                    out_mkv=Path("out.mkv"),
                    subs=[(Path("a.srt"), "spa", "ES")],
                    video_codec_mode="hevc",
                    hevc_device="auto",
                )

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
