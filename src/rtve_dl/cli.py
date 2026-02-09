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
    p.add_argument("--series-slug", default=None, help="Override the series slug used for caching under data/series/")
    p.add_argument("--quality", default="mp4", choices=["mp4", "best"], help="Prefer progressive MP4 or use best-effort")
    p.add_argument("--debug", action="store_true", help="Print progress/stage information")
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
            codex_model=a.codex_model,
            codex_chunk_cues=a.codex_chunk_cues,
        )

    p.set_defaults(func=_cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
