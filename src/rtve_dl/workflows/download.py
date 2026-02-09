from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rtve_dl.ffmpeg import download_to_mp4, mux_mkv
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import SeriesAsset, list_assets_for_selector
from rtve_dl.rtve.resolve import RtveResolver
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import parse_vtt
from rtve_dl.log import debug, stage
from rtve_dl.codex_ru import translate_es_to_ru_with_codex
from rtve_dl.codex_en import translate_es_to_en_with_codex
from rtve_dl.asr_whisperx import transcribe_es_to_srt_with_whisperx
from rtve_dl.asr_mlx import transcribe_es_to_srt_with_mlx_whisper


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
    out: Path
    tmp: Path


def _paths_for(series_url: str, series_slug: str | None) -> SeriesPaths:
    slug = series_slug or _slugify(series_url)
    out_root = Path("data") / slug
    tmp_root = Path("tmp") / slug
    return SeriesPaths(
        slug=slug,
        out=out_root,
        tmp=tmp_root,
    )


def _ensure_dirs(p: SeriesPaths) -> None:
    p.tmp.mkdir(parents=True, exist_ok=True)
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
    require_ru: bool,
    translate_en_if_missing: bool,
    asr_if_missing: bool,
    asr_model: str,
    asr_device: str,
    asr_compute_type: str,
    asr_batch_size: int,
    asr_vad_method: str,
    asr_backend: str,
    asr_mlx_model: str,
    codex_model: str | None,
    codex_chunk_cues: int,
    parallel: bool,
    jobs_episodes: int,
    jobs_codex_chunks: int,
) -> int:
    http = HttpClient()
    with stage("catalog"):
        assets = list_assets_for_selector(series_url, selector, http=http)
    paths = _paths_for(series_url, series_slug)
    _ensure_dirs(paths)

    resolver = RtveResolver(http)
    is_season = "S" not in selector.upper()
    failures: list[str] = []
    progress_lock = threading.Lock()

    def _ep_log(tag: str, state: str) -> None:
        if not parallel:
            return
        with progress_lock:
            debug(f"ep:{tag} {state}")

    def _process_one(a: SeriesAsset) -> str | None:
        t0 = time.time()
        ep_tag = a.asset_id
        try:
            _ep_log(ep_tag, "start")
            _ep_log(ep_tag, "resolve")
            with stage(f"resolve:{a.asset_id}"):
                resolved = resolver.resolve(a.asset_id, ignore_drm=True)

            title = a.title or resolved.title or a.asset_id
            season_num = a.season or 0
            episode_num = a.episode or 0
            base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"
            ep_tag = base
            _ep_log(ep_tag, "resolved")

            out_mkv = paths.out / f"{base}.mkv"
            mp4_path = paths.tmp / f"{base}.mp4"
            srt_es = paths.tmp / f"{base}.spa.srt"
            if out_mkv.exists():
                debug(f"skip mkv exists: {out_mkv}")
                _ep_log(ep_tag, "done (cached mkv)")
                print(out_mkv)
                return None

            # Video + ES subtitles are independent, so run in parallel.
            def _task_video() -> None:
                video_url = _pick_video_url(resolved.video_urls, quality)
                if not mp4_path.exists():
                    with stage(f"download:video:{a.asset_id}"):
                        headers = {"Referer": "https://www.rtve.es/"}
                        download_to_mp4(video_url, mp4_path, headers=headers)
                else:
                    debug(f"cache hit mp4: {mp4_path}")

            def _task_es() -> list:
                if resolved.subtitles_es_vtt:
                    with stage(f"download:subs:es:{a.asset_id}"):
                        _download_sub_vtt(http, resolved.subtitles_es_vtt, paths.tmp / f"{a.asset_id}.es.vtt")
                    with stage(f"build:srt:es:{a.asset_id}"):
                        es_cues_local = parse_vtt((paths.tmp / f"{a.asset_id}.es.vtt").read_text(encoding="utf-8"))
                        if not srt_es.exists():
                            srt_es.write_text(cues_to_srt(es_cues_local), encoding="utf-8")
                        else:
                            debug(f"cache hit srt: {srt_es}")
                    return es_cues_local

                if not asr_if_missing:
                    raise RuntimeError(
                        f"missing Spanish subtitles for asset {a.asset_id}; enable fallback with --asr-if-missing"
                    )
                with stage(f"build:srt:es_asr:{a.asset_id}"):
                    if not srt_es.exists():
                        if asr_backend == "mlx":
                            transcribe_es_to_srt_with_mlx_whisper(
                                media_path=mp4_path,
                                out_srt=srt_es,
                                model_repo=asr_mlx_model,
                            )
                        elif asr_backend == "whisperx":
                            transcribe_es_to_srt_with_whisperx(
                                media_path=mp4_path,
                                out_srt=srt_es,
                                model=asr_model,
                                device=asr_device,
                                compute_type=asr_compute_type,
                                batch_size=asr_batch_size,
                                vad_method=asr_vad_method,
                            )
                        else:
                            raise RuntimeError(f"unsupported ASR backend: {asr_backend}")
                    else:
                        debug(f"cache hit srt: {srt_es}")
                    return parse_srt(srt_es.read_text(encoding="utf-8"))

            _ep_log(ep_tag, "video+es")
            if parallel:
                with ThreadPoolExecutor(max_workers=2) as ep_pool:
                    fut_video = ep_pool.submit(_task_video)
                    fut_es = ep_pool.submit(_task_es)
                    es_cues = fut_es.result()
                    fut_video.result()
            else:
                _task_video()
                es_cues = _task_es()

            subs = [(srt_es, "spa", "Spanish")]

            def _task_en() -> tuple[Path, str, str] | None:
                if resolved.subtitles_en_vtt:
                    with stage(f"download:subs:en:{a.asset_id}"):
                        _download_sub_vtt(http, resolved.subtitles_en_vtt, paths.tmp / f"{a.asset_id}.en.vtt")
                    with stage(f"build:srt:en:{a.asset_id}"):
                        en_vtt = paths.tmp / f"{a.asset_id}.en.vtt"
                        en_cues = parse_vtt(en_vtt.read_text(encoding="utf-8"))
                        srt_en = paths.tmp / f"{base}.eng.srt"
                        if not srt_en.exists():
                            srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                        else:
                            debug(f"cache hit srt: {srt_en}")
                        return (srt_en, "eng", "English")

                if not translate_en_if_missing:
                    return None

                # Fallback: machine-translate ES -> EN using Codex chunks.
                with stage(f"build:srt:en_mt:{a.asset_id}"):
                    srt_en = paths.tmp / f"{base}.eng.srt"
                    if not srt_en.exists():
                        cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(es_cues) if (c.text or "").strip()]
                        base_path = paths.tmp / f"{base}.en"
                        en_map = (
                            translate_es_to_en_with_codex(
                                cues=cue_tasks,
                                base_path=base_path,
                                chunk_size_cues=codex_chunk_cues,
                                model=codex_model,
                                resume=True,
                                max_workers=jobs_codex_chunks,
                            )
                            if cue_tasks
                            else {}
                        )

                        from rtve_dl.subs.vtt import Cue

                        en_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=en_map.get(f"{i}", ""))
                            for i, c in enumerate(es_cues)
                        ]
                        srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_en}")
                    return (srt_en, "eng", "English (MT)")

            def _task_ru() -> list[tuple[Path, str, str]]:
                if not with_ru:
                    if require_ru:
                        raise RuntimeError("RU subtitles are required but disabled (--no-with-ru)")
                    return []
                with stage(f"build:srt:ru:{a.asset_id}"):
                    srt_ru = paths.tmp / f"{base}.rus.srt"
                    srt_bi = paths.tmp / f"{base}.spa_rus.srt"
                    if not srt_ru.exists() or not srt_bi.exists():
                        cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(es_cues) if (c.text or "").strip()]
                        base_path = paths.tmp / f"{base}.ru"
                        ru_map = (
                            translate_es_to_ru_with_codex(
                                cues=cue_tasks,
                                base_path=base_path,
                                chunk_size_cues=codex_chunk_cues,
                                model=codex_model,
                                resume=True,
                                max_workers=jobs_codex_chunks,
                            )
                            if cue_tasks
                            else {}
                        )

                        from rtve_dl.subs.vtt import Cue

                        ru_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru_map.get(f"{i}", ""))
                            for i, c in enumerate(es_cues)
                        ]
                        bi_cues = [
                            Cue(
                                start_ms=c.start_ms,
                                end_ms=c.end_ms,
                                text=(c.text or "").strip() + "\n" + (ru_map.get(f"{i}", "") or "").strip(),
                            )
                            for i, c in enumerate(es_cues)
                        ]
                        if not srt_ru.exists():
                            srt_ru.write_text(cues_to_srt(ru_cues), encoding="utf-8")
                        if not srt_bi.exists():
                            srt_bi.write_text(cues_to_srt(bi_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_ru}")
                        debug(f"cache hit srt: {srt_bi}")
                    return [(srt_ru, "rus", "Russian"), (srt_bi, "rus", "Spanish|Russian")]

            _ep_log(ep_tag, "translations")
            if parallel:
                with ThreadPoolExecutor(max_workers=2) as tr_pool:
                    fut_ru = tr_pool.submit(_task_ru)
                    fut_en = tr_pool.submit(_task_en)
                    try:
                        en_track = fut_en.result()
                        if en_track is not None:
                            subs.append(en_track)
                    except Exception as e:
                        # EN fallback errors should not fail episode.
                        print(f"[warn] {a.asset_id}: EN subtitle fallback failed: {e}")
                    ru_tracks = fut_ru.result()
                    subs.extend(ru_tracks)
            else:
                try:
                    en_track = _task_en()
                    if en_track is not None:
                        subs.append(en_track)
                except Exception as e:
                    print(f"[warn] {a.asset_id}: EN subtitle fallback failed: {e}")
                subs.extend(_task_ru())

            _ep_log(ep_tag, "mux")
            with stage(f"mux:{a.asset_id}"):
                tmp_out = Path(str(out_mkv) + ".partial.mkv")
                mux_mkv(video_path=mp4_path, out_mkv=tmp_out, subs=subs)
                tmp_out.replace(out_mkv)
            _ep_log(ep_tag, f"done ({time.time() - t0:.1f}s)")
            print(out_mkv)
            return None
        except Exception as e:
            msg = f"{a.asset_id}: {e}"
            _ep_log(ep_tag, f"fail ({time.time() - t0:.1f}s)")
            print(f"[error] {msg}")
            if not is_season:
                raise
            return msg

    if parallel and len(assets) > 1:
        workers = max(1, jobs_episodes)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {}
            for a in assets:
                _ep_log(a.asset_id, "queued")
                futs[ex.submit(_process_one, a)] = a.asset_id
            for fut in as_completed(futs):
                err = fut.result()
                if err:
                    failures.append(err)
    else:
        for a in assets:
            err = _process_one(a)
            if err:
                failures.append(err)

    return 1 if failures else 0
