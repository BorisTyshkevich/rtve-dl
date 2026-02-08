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

    def _cmd_download(a: argparse.Namespace) -> int:
        set_debug(a.debug)
        return download_selector(a.series_url, a.selector, series_slug=a.series_slug, quality=a.quality)

    p.set_defaults(func=_cmd_download)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
