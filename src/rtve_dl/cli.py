from __future__ import annotations

import argparse
from pathlib import Path

import re

from rtve_dl.rtve.catalog import build_series_index
from rtve_dl.lexicon.store import SeriesStore
from rtve_dl.workflows.download import download_selector
from rtve_dl.workflows.subtitles import build_subtitles_for_selector
from rtve_dl.workflows.mux_url import mux_from_url
from rtve_dl.ffmpeg import mux_mkv
from rtve_dl.subs.mine_phrases import mine_phrases_into_terms
from rtve_dl.lexicon.sync import sync_lexicon_tsv_into_gloss
from rtve_dl.lexicon.phrases import clear_mined_phrases, export_phrase_candidates_tsv
from rtve_dl.translate.batch import (
    export_gloss_tasks,
    import_gloss_results,
    export_ru_tasks,
    import_ru_results,
)

def _is_url(s: str) -> bool:
    return "://" in (s or "")


def _open_store(series_or_slug: str, *, series_slug: str | None) -> SeriesStore:
    if _is_url(series_or_slug):
        return SeriesStore.open_or_create(series_url=series_or_slug, series_slug=series_slug)
    return SeriesStore.open_existing(series_or_slug)


def _cmd_index(args: argparse.Namespace) -> int:
    store = SeriesStore.open_or_create(series_url=args.series_url, series_slug=args.series_slug)
    build_series_index(store, max_assets=args.max_assets, selector=args.selector)
    print(store.series_slug)
    return 0


def _cmd_export_gloss(args: argparse.Namespace) -> int:
    store = SeriesStore.open_existing(args.series_slug)
    out_path = export_gloss_tasks(store, threshold=args.threshold)
    print(out_path)
    return 0


def _cmd_import_gloss(args: argparse.Namespace) -> int:
    store = SeriesStore.open_existing(args.series_slug)
    import_gloss_results(store, args.results_jsonl)
    return 0


def _cmd_export_ru(args: argparse.Namespace) -> int:
    store = SeriesStore.open_existing(args.series_slug)
    out_path = export_ru_tasks(store)
    print(out_path)
    return 0


def _cmd_import_ru(args: argparse.Namespace) -> int:
    store = SeriesStore.open_existing(args.series_slug)
    import_ru_results(store, args.results_jsonl)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtve_dl")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="Download subtitles and build a per-series term dataset")
    p.add_argument("series_url")
    p.add_argument("--series-slug", default=None, help="Override the series slug used for data storage")
    p.add_argument("--selector", default=None, help="Limit indexing: T7 for season 7, or T7S5 for episode 5 of season 7")
    p.add_argument("--max-assets", type=int, default=None, help="Limit number of assets (debugging)")
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("mine-phrases", help="Mine frequent phrase candidates (n-grams) from indexed Spanish cues")
    p.add_argument("series", help="Series URL (recommended) or an existing series slug")
    p.add_argument("--series-slug", default=None, help="When `series` is a URL, override the series slug used for data storage")
    p.add_argument("--selector", default=None, help="Limit mining: T7 for season 7, or T7S5 for episode 5 of season 7")
    p.add_argument("--min-n", type=int, default=2)
    p.add_argument("--max-n", type=int, default=5)
    p.add_argument("--min-count", type=int, default=5)
    p.add_argument("--limit", type=int, default=5000)

    def _cmd_mine_phrases(a: argparse.Namespace) -> int:
        store = _open_store(a.series, series_slug=a.series_slug)
        con = store.connect()
        try:
            stop = store.load_stopwords()
            asset_ids = None
            if a.selector:
                m = re.match(r"^T(\d+)(?:S(\d+))?$", a.selector.strip(), re.IGNORECASE)
                if not m:
                    raise SystemExit("selector must look like T7 or T7S5")
                season = int(m.group(1))
                episode = int(m.group(2)) if m.group(2) else None
                if episode is None:
                    rows = con.execute("SELECT asset_id FROM assets WHERE season=?", (season,)).fetchall()
                else:
                    rows = con.execute("SELECT asset_id FROM assets WHERE season=? AND episode=?", (season, episode)).fetchall()
                asset_ids = {r["asset_id"] for r in rows}
            n = mine_phrases_into_terms(
                con,
                stopwords=stop,
                asset_ids=asset_ids,
                min_n=a.min_n,
                max_n=a.max_n,
                min_count=a.min_count,
                limit=a.limit,
            )
            con.commit()
        finally:
            con.close()
        print(n)
        return 0

    p.set_defaults(func=_cmd_mine_phrases)

    p = sub.add_parser("phrases-clear", help="Delete mined phrase candidates from the terms table (safe)")
    p.add_argument("series_slug")

    def _cmd_phrases_clear(a: argparse.Namespace) -> int:
        store = SeriesStore.open_existing(a.series_slug)
        con = store.connect()
        try:
            n = clear_mined_phrases(con)
            con.commit()
        finally:
            con.close()
        print(n)
        return 0

    p.set_defaults(func=_cmd_phrases_clear)

    p = sub.add_parser("phrases-export", help="Export mined phrase candidates to a TSV for manual curation")
    p.add_argument("series_slug")
    p.add_argument("--out", default=None, help="Output TSV path (default: data/series/<slug>/phrase_candidates.tsv)")
    p.add_argument("--min-count", type=int, default=2)
    p.add_argument("--limit", type=int, default=2000)

    def _cmd_phrases_export(a: argparse.Namespace) -> int:
        store = SeriesStore.open_existing(a.series_slug)
        out = (
            (store.root_dir / "phrase_candidates.tsv")
            if a.out is None
            else Path(a.out)
        )
        con = store.connect()
        try:
            export_phrase_candidates_tsv(con, out_path=out, limit=a.limit, min_count=a.min_count)
        finally:
            con.close()
        print(out)
        return 0

    p.set_defaults(func=_cmd_phrases_export)

    p = sub.add_parser("lexicon-sync", help="Sync editable TSV lexicon files into the gloss database")
    p.add_argument("series_slug")

    def _cmd_lexicon_sync(a: argparse.Namespace) -> int:
        store = SeriesStore.open_existing(a.series_slug)
        con = store.connect()
        try:
            n1 = sync_lexicon_tsv_into_gloss(con, kind="word", tsv_path=str(store.root_dir / "lexicon_words.tsv"))
            n2 = sync_lexicon_tsv_into_gloss(con, kind="phrase", tsv_path=str(store.root_dir / "lexicon_phrases.tsv"))
            con.commit()
        finally:
            con.close()
        print(f"words={n1} phrases={n2}")
        return 0

    p.set_defaults(func=_cmd_lexicon_sync)

    p = sub.add_parser("download", help="Download video and mux MKV with subtitles")
    p.add_argument("series", help="Series URL (recommended) or an existing series slug")
    p.add_argument("selector", help="T7 for season 7, or T7S5 for episode 5 of season 7")
    p.add_argument("--series-slug", default=None, help="When `series` is a URL, override the series slug used for data storage")
    p.add_argument("--quality", default="best", help="best|m3u8|mp4 (initial heuristic)")
    p.add_argument(
        "--ignore-drm",
        action="store_true",
        help="Attempt download even if RTVE metadata marks the asset as DRM (no DRM circumvention; best-effort)",
    )
    p.add_argument("--with-ru", action="store_true", help="Include full Russian subtitle translation track")
    p.add_argument(
        "--require-ru",
        action="store_true",
        help="Fail if RU translation track is incomplete (implies --with-ru)",
    )
    def _cmd_download(a: argparse.Namespace) -> int:
        store = _open_store(a.series, series_slug=a.series_slug)
        return download_selector(
            store.series_slug,
            a.selector,
            a.quality,
            with_ru=(a.with_ru or a.require_ru),
            require_ru=a.require_ru,
            ignore_drm=a.ignore_drm,
        )

    p.set_defaults(func=_cmd_download)

    p = sub.add_parser("subs", help="Build subtitle files (SRT) without downloading video")
    p.add_argument("series", help="Series URL (recommended) or an existing series slug")
    p.add_argument("selector")
    p.add_argument("--with-ru", action="store_true", help="Include full Russian subtitle translation track")
    p.add_argument("--require-ru", action="store_true", help="Fail if RU translation track is incomplete (implies --with-ru)")
    p.add_argument("--series-slug", default=None, help="When `series` is a URL, override the series slug used for data storage")

    def _cmd_subs(a: argparse.Namespace) -> int:
        store = _open_store(a.series, series_slug=a.series_slug)
        paths = build_subtitles_for_selector(
            store.series_slug, a.selector, with_ru=(a.with_ru or a.require_ru), require_ru=a.require_ru
        )
        for pth in paths:
            print(pth)
        return 0

    p.set_defaults(func=_cmd_subs)

    p = sub.add_parser("mux-local", help="Mux a local video file with built subtitles into MKV")
    p.add_argument("series_slug")
    p.add_argument("selector", help="Must be a single episode selector like T7S5")
    p.add_argument("--video", required=True, help="Path to your local legal video file (mp4/mkv/...) to mux")
    p.add_argument("--out", default=None, help="Output MKV path (default: data/series/<slug>/out/<base>.local.mkv)")
    p.add_argument("--with-ru", action="store_true", help="Include full Russian subtitle translation track")
    p.add_argument("--require-ru", action="store_true", help="Fail if RU translation track is incomplete (implies --with-ru)")

    def _cmd_mux_local(a: argparse.Namespace) -> int:
        if "S" not in a.selector.upper():
            raise SystemExit("mux-local currently supports a single episode selector (e.g. T7S5)")
        store = SeriesStore.open_existing(a.series_slug)
        build_subtitles_for_selector(a.series_slug, a.selector, with_ru=(a.with_ru or a.require_ru), require_ru=a.require_ru)

        tmp_dir = store.root_dir / "tmp"
        spa = sorted(tmp_dir.glob("S??E??_*.spa.srt"))
        if not spa:
            raise SystemExit("no subtitles found; run `rtve_dl subs` first")
        base = spa[0].name[: -len(".spa.srt")]
        out_mkv = (store.root_dir / "out" / f"{base}.local.mkv") if a.out is None else Path(a.out)

        subs = []
        # Keep deterministic order.
        want = [
            (f"{base}.spa.srt", "spa", "Spanish"),
            (f"{base}.spa.ru_a1plus.srt", "spa", "Spanish (RU A1+)"),
            (f"{base}.spa.ru_a2plus.srt", "spa", "Spanish (RU A2+)"),
            (f"{base}.spa.ru_b1plus.srt", "spa", "Spanish (RU B1+)"),
            (f"{base}.rus.srt", "rus", "Russian"),
            (f"{base}.eng.srt", "eng", "English"),
        ]
        for fname, lang, title in want:
            pth = tmp_dir / fname
            if pth.exists():
                if fname.endswith(".rus.srt") and not (a.with_ru or a.require_ru):
                    continue
                subs.append((pth, lang, title))

        mux_mkv(video_path=Path(a.video), out_mkv=out_mkv, subs=subs)
        print(out_mkv)
        return 0

    p.set_defaults(func=_cmd_mux_local)

    p = sub.add_parser("mux-url", help="Download video from a user-supplied URL (with cookies) and mux with subtitles")
    p.add_argument("series_slug")
    p.add_argument("selector", help="Must be a single episode selector like T7S5")
    p.add_argument("--url", required=True, help="Direct mp4/m3u8 URL you already have access to")
    p.add_argument("--cookie", default=None, help="Raw Cookie header value for curl (-b). Prefer --cookie-file.")
    p.add_argument("--cookie-file", default=None, help="Netscape cookies.txt file for curl (-b)")
    p.add_argument("--header", action="append", default=[], help="Extra HTTP header for curl (repeatable), e.g. 'Referer: https://www.rtve.es/'")
    p.add_argument("--out", default=None, help="Output MKV path (default: data/series/<slug>/out/<base>.url.mkv)")
    p.add_argument("--with-ru", action="store_true", help="Include full Russian subtitle translation track")
    p.add_argument("--require-ru", action="store_true", help="Fail if RU translation track is incomplete (implies --with-ru)")

    def _cmd_mux_url(a: argparse.Namespace) -> int:
        return mux_from_url(
            series_slug=a.series_slug,
            selector=a.selector,
            url=a.url,
            cookie_file=a.cookie_file,
            cookie=a.cookie,
            headers=list(a.header or []),
            out=a.out,
            with_ru=(a.with_ru or a.require_ru),
            require_ru=a.require_ru,
        )

    p.set_defaults(func=_cmd_mux_url)

    p = sub.add_parser("export-gloss", help="Export JSONL tasks for CEFR labeling + RU glossing")
    p.add_argument("series_slug")
    p.add_argument("--threshold", choices=["A2", "B1", "B2"], default="A2")
    p.set_defaults(func=_cmd_export_gloss)

    p = sub.add_parser("import-gloss", help="Import JSONL results for CEFR labeling + RU glossing")
    p.add_argument("series_slug")
    p.add_argument("results_jsonl")
    p.set_defaults(func=_cmd_import_gloss)

    p = sub.add_parser("export-ru", help="Export JSONL tasks for full Russian subtitle translation")
    p.add_argument("series_slug")
    p.set_defaults(func=_cmd_export_ru)

    p = sub.add_parser("import-ru", help="Import JSONL results for full Russian subtitle translation")
    p.add_argument("series_slug")
    p.add_argument("results_jsonl")
    p.set_defaults(func=_cmd_import_ru)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
