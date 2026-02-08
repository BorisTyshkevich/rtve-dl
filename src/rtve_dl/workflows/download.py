from __future__ import annotations

import re
from dataclasses import dataclass
import json
from pathlib import Path

from rtve_dl.ffmpeg import download_to_mp4, mux_mkv
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import SeriesAsset, list_assets_for_selector
from rtve_dl.rtve.resolve import RtveResolver
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.vtt import parse_vtt
from rtve_dl.log import debug, stage
from rtve_dl.ru import setup_argos_model, translate_cues_jsonl


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
    with_ru: bool,
    argos_model: str | None,
    translate_en_if_missing: bool,
) -> int:
    http = HttpClient()
    with stage("catalog"):
        assets = list_assets_for_selector(series_url, selector, http=http)
    paths = _paths_for(series_url, series_slug)
    _ensure_dirs(paths)

    resolver = RtveResolver(http)

    for a in assets:
        with stage(f"resolve:{a.asset_id}"):
            resolved = resolver.resolve(a.asset_id, ignore_drm=True)

        title = a.title or resolved.title or a.asset_id
        season_num = a.season or 0
        episode_num = a.episode or 0
        base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"

        out_mkv = paths.out / f"{base}.mkv"
        if out_mkv.exists():
            debug(f"skip mkv exists: {out_mkv}")
            print(out_mkv)
            continue

        # Video (cached mp4).
        video_url = _pick_video_url(resolved.video_urls, quality)
        mp4_path = paths.tmp / f"{base}.mp4"
        if not mp4_path.exists():
            with stage(f"download:video:{a.asset_id}"):
                headers = {"Referer": "https://www.rtve.es/"}
                download_to_mp4(video_url, mp4_path, headers=headers)
        else:
            debug(f"cache hit mp4: {mp4_path}")

        # Subtitles (cached vtt + cached srt).
        if not resolved.subtitles_es_vtt:
            raise SystemExit(f"missing Spanish subtitles for asset {a.asset_id}")
        with stage(f"download:subs:es:{a.asset_id}"):
            _download_sub_vtt(http, resolved.subtitles_es_vtt, paths.subs / f"{a.asset_id}.es.vtt")

        with stage(f"build:srt:es:{a.asset_id}"):
            es_cues = parse_vtt((paths.subs / f"{a.asset_id}.es.vtt").read_text(encoding="utf-8"))
            srt_es = paths.tmp / f"{base}.spa.srt"
            if not srt_es.exists():
                srt_es.write_text(cues_to_srt(es_cues), encoding="utf-8")
            else:
                debug(f"cache hit srt: {srt_es}")

        subs = [(srt_es, "spa", "Spanish")]

        en_cues = None
        if resolved.subtitles_en_vtt:
            with stage(f"download:subs:en:{a.asset_id}"):
                _download_sub_vtt(http, resolved.subtitles_en_vtt, paths.subs / f"{a.asset_id}.en.vtt")
            with stage(f"build:srt:en:{a.asset_id}"):
                en_vtt = paths.subs / f"{a.asset_id}.en.vtt"
                if en_vtt.exists():
                    en_cues = parse_vtt(en_vtt.read_text(encoding="utf-8"))
                    srt_en = paths.tmp / f"{base}.eng.srt"
                    if not srt_en.exists():
                        srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_en}")
                    subs.append((srt_en, "eng", "English"))
        elif translate_en_if_missing:
            # Produce a machine English track only if RTVE did not provide one.
            with stage("argos:ensure-model"):
                setup_argos_model(Path("."), model_path=argos_model)
            with stage(f"build:srt:en_mt:{a.asset_id}"):
                srt_en = paths.tmp / f"{base}.eng.srt"
                if not srt_en.exists():
                    out_jsonl = paths.tmp / f"{base}.en.jsonl"
                    cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(es_cues)]
                    translate_cues_jsonl(Path("."), cues=cue_tasks, src="es", dst="en", out_jsonl=out_jsonl)
                    mt_map: dict[int, str] = {}
                    for line in out_jsonl.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        obj = json.loads(line)
                        try:
                            idx = int(obj["id"])
                        except Exception:
                            continue
                        mt_map[idx] = obj.get("text") or ""
                    if len(mt_map) != len(es_cues):
                        raise SystemExit(
                            f"Argos EN translation incomplete for asset {a.asset_id}: got {len(mt_map)}/{len(es_cues)} cues"
                        )
                    from rtve_dl.subs.vtt import Cue

                    en_cues = [Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=mt_map.get(i, "")) for i, c in enumerate(es_cues)]
                    srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                else:
                    debug(f"cache hit srt: {srt_en}")
                # If we produced EN cues here, include them.
                if srt_en.exists():
                    subs.append((srt_en, "eng", "English"))

        if with_ru:
            with stage("argos:ensure-model"):
                setup_argos_model(Path("."), model_path=argos_model)
            with stage(f"build:srt:ru:{a.asset_id}"):
                srt_ru = paths.tmp / f"{base}.rus.srt"
                if not srt_ru.exists():
                    # Translate cue-by-cue to keep timings identical to ES.
                    from rtve_dl.subs.vtt import Cue

                    # Prefer translating from RTVE English subtitles if present (en->ru),
                    # otherwise translate Spanish (es->ru, Argos will pivot if needed).
                    src_lang = "en" if en_cues is not None else "es"
                    src_cues = en_cues if en_cues is not None else es_cues
                    out_jsonl = paths.tmp / f"{base}.ru.jsonl"
                    cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(src_cues)]
                    translate_cues_jsonl(Path("."), cues=cue_tasks, src=src_lang, dst="ru", out_jsonl=out_jsonl)
                    ru_map: dict[int, str] = {}
                    for line in out_jsonl.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        obj = json.loads(line)
                        try:
                            idx = int(obj["id"])
                        except Exception:
                            continue
                        ru_map[idx] = obj.get("text") or ""
                    if len(ru_map) != len(src_cues):
                        raise SystemExit(
                            f"Argos translation incomplete for asset {a.asset_id}: got {len(ru_map)}/{len(src_cues)} cues"
                        )

                    ru_lines: list[Cue] = []
                    for i, c in enumerate(src_cues):
                        ru_lines.append(Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru_map.get(i, "")))
                    srt_ru.write_text(cues_to_srt(ru_lines), encoding="utf-8")
                else:
                    debug(f"cache hit srt: {srt_ru}")
                subs.append((srt_ru, "rus", "Russian"))

        with stage(f"mux:{a.asset_id}"):
            mux_mkv(video_path=mp4_path, out_mkv=out_mkv, subs=subs)
        print(out_mkv)

    return 0
