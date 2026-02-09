from __future__ import annotations

import argparse

from rtve_dl.workflows.download import download_selector
from rtve_dl.log import set_debug


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtve_dl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download", help="Download video + ES/EN subtitles and mux MKV")
    p.add_argument("series_url", help="Series page URL, e.g. https://www.rtve.es/play/videos/cuentame-como-paso/")
    p.add_argument("selector", help="T7 for a season, or T7S5 for an episode")
    p.add_argument("--series-slug", default=None, help="Override series slug used in data/<slug>/ and tmp/<slug>/")
    p.add_argument("--quality", default="mp4", choices=["mp4", "best"], help="Prefer progressive MP4 or use best-effort")
    p.add_argument("--debug", action="store_true", help="Print progress/stage information")
    p.add_argument(
        "--asr-if-missing",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If RTVE has no ES subtitles, generate ES subtitles with ASR backend. Default: enabled.",
    )
    p.add_argument(
        "--asr-backend",
        default="mlx",
        choices=["mlx", "whisperx"],
        help="ASR backend for missing ES subtitles (default: mlx)",
    )
    p.add_argument(
        "--asr-mlx-model",
        default="mlx-community/whisper-small",
        help="MLX Whisper model repo (used when --asr-backend mlx)",
    )
    p.add_argument("--asr-model", default="large-v3", help="WhisperX model for ES subtitle fallback")
    p.add_argument("--asr-device", default="cpu", help="WhisperX device (default: cpu)")
    p.add_argument(
        "--asr-compute-type",
        default="float32",
        help="WhisperX compute type (default: float32)",
    )
    p.add_argument("--asr-batch-size", type=int, default=8, help="WhisperX batch size (default: 8)")
    p.add_argument(
        "--asr-vad-method",
        default="silero",
        choices=["silero", "pyannote"],
        help="WhisperX VAD method (default: silero)",
    )
    p.add_argument(
        "--translate-en-if-missing",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="If RTVE doesn't provide English subs, translate ES->EN via Codex. Default: enabled.",
    )
    p.add_argument(
        "--with-ru",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Add Russian subtitle track (Codex batch). Default: enabled.",
    )
    p.add_argument(
        "--require-ru",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Fail an episode if Russian subtitles could not be generated. Default: enabled.",
    )
    p.add_argument("--codex-model", default=None, help="Override Codex model for `codex exec` (optional)")
    p.add_argument(
        "--codex-chunk-cues",
        type=int,
        default=400,
        help="Chunk size in cues for batch translation (default: 400)",
    )

    def _cmd_download(a: argparse.Namespace) -> int:
        set_debug(a.debug)
        return download_selector(
            a.series_url,
            a.selector,
            series_slug=a.series_slug,
            quality=a.quality,
            with_ru=a.with_ru,
            require_ru=a.require_ru,
            translate_en_if_missing=a.translate_en_if_missing,
            asr_if_missing=a.asr_if_missing,
            asr_model=a.asr_model,
            asr_device=a.asr_device,
            asr_compute_type=a.asr_compute_type,
            asr_batch_size=a.asr_batch_size,
            asr_vad_method=a.asr_vad_method,
            asr_backend=a.asr_backend,
            asr_mlx_model=a.asr_mlx_model,
            codex_model=a.codex_model,
            codex_chunk_cues=a.codex_chunk_cues,
        )

    p.set_defaults(func=_cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
