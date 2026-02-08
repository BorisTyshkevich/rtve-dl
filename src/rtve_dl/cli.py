from __future__ import annotations

import argparse

from rtve_dl.workflows.download import download_selector
from rtve_dl.log import set_debug
from pathlib import Path

from rtve_dl.ru import setup_argos_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtve_dl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup-argos", help="Install Argos Translate Spanish->Russian model")
    p.add_argument("--model", default=None, help="Optional path to a local .argosmodel file")

    def _cmd_setup_argos(a: argparse.Namespace) -> int:
        setup_argos_model(Path("."), model_path=a.model)
        print("ok")
        return 0

    p.set_defaults(func=_cmd_setup_argos)

    p = sub.add_parser("download", help="Download video + ES/EN subtitles and mux MKV")
    p.add_argument("series_url", help="Series page URL, e.g. https://www.rtve.es/play/videos/cuentame-como-paso/")
    p.add_argument("selector", help="T7 for a season, or T7S5 for an episode")
    p.add_argument("--series-slug", default=None, help="Override the series slug used for caching under data/series/")
    p.add_argument("--quality", default="mp4", choices=["mp4", "best"], help="Prefer progressive MP4 or use best-effort")
    p.add_argument("--debug", action="store_true", help="Print progress/stage information")
    p.add_argument("--with-ru", action="store_true", help="Add Russian subtitle track (offline Argos Translate)")
    p.add_argument(
        "--translate-en-if-missing",
        action="store_true",
        help="If RTVE English subtitles are missing, generate an English track by translating Spanish (offline Argos)",
    )
    p.add_argument("--argos-model", default=None, help="Optional path to a local .argosmodel file to install (es->ru)")

    def _cmd_download(a: argparse.Namespace) -> int:
        set_debug(a.debug)
        return download_selector(
            a.series_url,
            a.selector,
            series_slug=a.series_slug,
            quality=a.quality,
            with_ru=a.with_ru,
            argos_model=a.argos_model,
            translate_en_if_missing=a.translate_en_if_missing,
        )

    p.set_defaults(func=_cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
