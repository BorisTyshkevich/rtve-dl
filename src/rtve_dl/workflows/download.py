from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rtve_dl.ffmpeg import download_to_mp4, mux_mkv
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import SeriesAsset, list_assets_for_selector
from rtve_dl.rtve.resolve import RtveResolver
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.vtt import parse_vtt


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:80] if s else "series"


def _slug_title(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:80] if s else "episode"


def _pick_video_url(urls: list[str], quality: str) -> str:
    # Prefer progressive MP4 for "mp4", otherwise a simple "best" heuristic.
    if quality == "mp4":
        for u in urls:
            if "rtve-mediavod-lote3.rtve.es" in u and ".mp4" in u:
                return u
        for u in urls:
            if ".mp4" in u:
                return u
    # best heuristic: prefer HLS master, then any m3u8, then mp4.
    for u in urls:
        if u.endswith(".m3u8") and "video.m3u8" in u:
            return u
    for u in urls:
        if ".m3u8" in u:
            return u
    return urls[0]


@dataclass(frozen=True)
class SeriesPaths:
    slug: str
    root: Path
    tmp: Path
    subs: Path
    out: Path


def _paths_for(series_url: str, series_slug: str | None) -> SeriesPaths:
    slug = series_slug or _slugify(series_url)
    root = Path("data") / "series" / slug
    return SeriesPaths(
        slug=slug,
        root=root,
        tmp=root / "tmp",
        subs=root / "subs",
        out=root / "out",
    )


def _ensure_dirs(p: SeriesPaths) -> None:
    p.tmp.mkdir(parents=True, exist_ok=True)
    p.subs.mkdir(parents=True, exist_ok=True)
    p.out.mkdir(parents=True, exist_ok=True)


def _download_sub_vtt(http: HttpClient, url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        return
    out_path.write_text(http.get_text(url), encoding="utf-8")


def download_selector(
    series_url: str,
    selector: str,
    *,
    series_slug: str | None,
    quality: str,
) -> int:
    http = HttpClient()
    assets = list_assets_for_selector(series_url, selector, http=http)
    paths = _paths_for(series_url, series_slug)
    _ensure_dirs(paths)

    resolver = RtveResolver(http)

    for a in assets:
        resolved = resolver.resolve(a.asset_id, ignore_drm=True)

        title = a.title or resolved.title or a.asset_id
        season_num = a.season or 0
        episode_num = a.episode or 0
        base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"

        out_mkv = paths.out / f"{base}.mkv"
        if out_mkv.exists():
            print(out_mkv)
            continue

        # Video (cached mp4).
        video_url = _pick_video_url(resolved.video_urls, quality)
        mp4_path = paths.tmp / f"{base}.mp4"
        if not mp4_path.exists():
            headers = {"Referer": "https://www.rtve.es/"}
            download_to_mp4(video_url, mp4_path, headers=headers)

        # Subtitles (cached vtt + cached srt).
        if not resolved.subtitles_es_vtt:
            raise SystemExit(f"missing Spanish subtitles for asset {a.asset_id}")
        _download_sub_vtt(http, resolved.subtitles_es_vtt, paths.subs / f"{a.asset_id}.es.vtt")

        es_cues = parse_vtt((paths.subs / f"{a.asset_id}.es.vtt").read_text(encoding="utf-8"))
        srt_es = paths.tmp / f"{base}.spa.srt"
        if not srt_es.exists():
            srt_es.write_text(cues_to_srt(es_cues), encoding="utf-8")

        subs = [(srt_es, "spa", "Spanish")]

        if resolved.subtitles_en_vtt:
            _download_sub_vtt(http, resolved.subtitles_en_vtt, paths.subs / f"{a.asset_id}.en.vtt")
            en_vtt = paths.subs / f"{a.asset_id}.en.vtt"
            if en_vtt.exists():
                en_cues = parse_vtt(en_vtt.read_text(encoding="utf-8"))
                srt_en = paths.tmp / f"{base}.eng.srt"
                if not srt_en.exists():
                    srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                subs.append((srt_en, "eng", "English"))

        mux_mkv(video_path=mp4_path, out_mkv=out_mkv, subs=subs)
        print(out_mkv)

    return 0

