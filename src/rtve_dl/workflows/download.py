from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rtve_dl.ffmpeg import download_to_mp4, is_valid_mp4, mux_mkv, probe_duration_seconds
from rtve_dl.http import HttpClient
from rtve_dl.rtve.catalog import SeriesAsset, list_assets_for_selector
from rtve_dl.rtve.resolve import RtveResolver
from rtve_dl.subs.srt import cues_to_srt
from rtve_dl.subs.srt_parse import parse_srt
from rtve_dl.subs.vtt import parse_vtt
from rtve_dl.subs.delay_auto import estimate_series_delay_ms
from rtve_dl.log import debug, error, stage
from rtve_dl.codex_ru import translate_es_to_ru_with_codex
from rtve_dl.codex_ru_refs import translate_es_to_ru_refs_with_codex
from rtve_dl.codex_en import translate_es_to_en_with_codex
from rtve_dl.codex_es_clean import clean_es_with_codex
from rtve_dl.codex_batch import CodexExecutionContext
from rtve_dl.asr_whisperx import transcribe_es_to_srt_with_whisperx
from rtve_dl.asr_mlx import transcribe_es_to_srt_with_mlx_whisper
from rtve_dl.index_html import build_slug_index
from rtve_dl.global_phrase_cache import GlobalPhraseCache, load_global_phrase_cache
from rtve_dl.telemetry import TelemetryDB
from rtve_dl.tmp_layout import TmpLayout, migrate_tmp_slug_layout


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
    layout: TmpLayout


def _paths_for(series_url: str, series_slug: str | None) -> SeriesPaths:
    slug = series_slug or _slugify(series_url)
    out_root = Path("data") / slug
    tmp_root = Path("tmp") / slug
    layout = TmpLayout.for_slug(tmp_root)
    return SeriesPaths(
        slug=slug,
        out=out_root,
        tmp=tmp_root,
        layout=layout,
    )


def _ensure_dirs(p: SeriesPaths) -> None:
    p.layout.ensure_dirs()
    migrate_tmp_slug_layout(p.layout)
    p.out.mkdir(parents=True, exist_ok=True)


def _is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _remove_if_empty(path: Path, *, kind: str) -> None:
    if not path.exists():
        return
    try:
        if path.stat().st_size > 0:
            return
    except OSError:
        return
    error(f"removing empty {kind} cache file: {path}")
    try:
        path.unlink()
    except OSError:
        pass


_RESET_LAYER_ALLOWED = {
    "subs-es",
    "subs-en",
    "subs-ru",
    "subs-refs",
    "video",
    "mkv",
    "catalog",
}


def _normalize_reset_layers(raw_layers: list[str] | None) -> set[str]:
    out: set[str] = set()
    for raw in raw_layers or []:
        for part in (raw or "").split(","):
            v = part.strip().lower()
            if v:
                out.add(v)
    unknown = sorted(v for v in out if v not in _RESET_LAYER_ALLOWED)
    if unknown:
        allowed = ", ".join(sorted(_RESET_LAYER_ALLOWED))
        raise RuntimeError(f"unknown reset layer(s): {', '.join(unknown)}. Allowed: {allowed}")
    return out


def _expand_reset_layers(user_layers: set[str]) -> set[str]:
    expanded = set(user_layers)
    changed = True
    while changed:
        changed = False
        prev = set(expanded)
        if "video" in expanded:
            expanded.update({"subs-es", "subs-en", "subs-ru", "subs-refs", "mkv"})
        if "subs-es" in expanded:
            expanded.update({"subs-en", "subs-ru", "subs-refs", "mkv"})
        if "subs-en" in expanded:
            expanded.add("mkv")
        if "subs-ru" in expanded:
            expanded.add("mkv")
        if "subs-refs" in expanded:
            expanded.add("mkv")
        changed = expanded != prev
    return expanded


def _safe_unlink(path: Path, *, reason: str) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
        debug(f"reset:{reason} removed {path}")
    except OSError as e:
        error(f"reset:{reason} failed to remove {path}: {e}")


def _safe_unlink_glob(
    directory: Path,
    pattern: str,
    *,
    reason: str,
    exclude_prefix: str | None = None,
    exclude_contains: str | None = None,
) -> None:
    for p in directory.glob(pattern):
        if exclude_prefix and p.name.startswith(exclude_prefix):
            continue
        if exclude_contains and exclude_contains in p.name:
            continue
        _safe_unlink(p, reason=reason)


def _reset_catalog_layer(paths: SeriesPaths) -> None:
    _safe_unlink_glob(paths.layout.meta, "catalog_*.json", reason="catalog")


def _episode_prefix(a: SeriesAsset) -> str:
    season_num = a.season or 0
    episode_num = a.episode or 0
    return f"S{season_num:02d}E{episode_num:02d}_"


def _reset_selector_layers(*, paths: SeriesPaths, assets: list[SeriesAsset], layers: set[str]) -> None:
    if not layers:
        return
    debug(f"reset:preflight start layers={','.join(sorted(layers))} episodes={len(assets)}")
    for a in assets:
        prefix = _episode_prefix(a)
        asset_id = a.asset_id

        if "mkv" in layers:
            _safe_unlink_glob(paths.out, f"{prefix}*.mkv", reason="mkv")
            _safe_unlink_glob(paths.out, f"{prefix}*.mkv.partial.mkv", reason="mkv")

        if "video" in layers:
            _safe_unlink_glob(paths.layout.mp4, f"{prefix}*.mp4", reason="video")
            _safe_unlink_glob(paths.layout.mp4, f"{prefix}*.mp4.partial.mp4", reason="video")

        if "subs-es" in layers:
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa.srt", reason="subs-es")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa.asr.srt", reason="subs-es")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa.asr_raw.srt", reason="subs-es")
            _safe_unlink(paths.layout.vtt_es_file(asset_id), reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_es_clean, f"{prefix}*.es_clean*", reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_en, f"{prefix}*.en*", reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_ru_ref, f"{prefix}*.ru_ref*", reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_ru, f"{prefix}*.ru*", reason="subs-es")
            # ASR-based translation caches
            _safe_unlink_glob(paths.layout.codex_en_asr, f"{prefix}*.en_asr*", reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_ru_asr, f"{prefix}*.ru_asr*", reason="subs-es")
            _safe_unlink_glob(paths.layout.codex_ru_ref_asr, f"{prefix}*.ru_ref_asr*", reason="subs-es")

        if "subs-en" in layers:
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.eng.srt", reason="subs-en")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.eng.asr.srt", reason="subs-en")
            _safe_unlink(paths.layout.vtt_en_file(asset_id), reason="subs-en")
            _safe_unlink_glob(paths.layout.codex_en, f"{prefix}*.en*", reason="subs-en")
            _safe_unlink_glob(paths.layout.codex_en_asr, f"{prefix}*.en_asr*", reason="subs-en")

        if "subs-ru" in layers:
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.rus.srt", reason="subs-ru")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.rus.asr.srt", reason="subs-ru")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa_rus_full.srt", reason="subs-ru")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa_rus_full.asr.srt", reason="subs-ru")
            _safe_unlink_glob(paths.layout.codex_ru, f"{prefix}*.ru*", reason="subs-ru")
            _safe_unlink_glob(paths.layout.codex_ru_asr, f"{prefix}*.ru_asr*", reason="subs-ru")

        if "subs-refs" in layers:
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa_rus.srt", reason="subs-refs")
            _safe_unlink_glob(paths.layout.srt, f"{prefix}*.spa_rus.asr.srt", reason="subs-refs")
            _safe_unlink_glob(paths.layout.codex_ru_ref, f"{prefix}*.ru_ref*", reason="subs-refs")
            _safe_unlink_glob(paths.layout.codex_ru_ref_asr, f"{prefix}*.ru_ref_asr*", reason="subs-refs")
    debug("reset:preflight done")


def _download_sub_vtt(http: HttpClient, url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_if_empty(out_path, kind="vtt")
    if out_path.exists():
        return
    out_path.write_text(http.get_text(url), encoding="utf-8")


def _collect_local_subs_for_mux(
    *,
    base: str,
    paths: SeriesPaths,
    with_ru: bool,
    translate_en_if_missing: bool,
    es_model_name: str = "RTVE",
    primary_model: str = "sonnet",
    force_asr: bool = False,
) -> tuple[list[tuple[Path, str, str]], str] | None:
    """Returns (subs_list, default_subtitle_title) or None if required files missing."""
    srt_es = paths.layout.srt_es_file(base)
    srt_en = paths.layout.srt_en_file(base)
    srt_ru = paths.layout.srt_ru_file(base)
    srt_bi = paths.layout.srt_refs_file(base)
    srt_bi_full = paths.layout.srt_bi_full_file(base)
    _remove_if_empty(srt_es, kind="srt")
    _remove_if_empty(srt_en, kind="srt")
    _remove_if_empty(srt_ru, kind="srt")
    _remove_if_empty(srt_bi, kind="srt")
    _remove_if_empty(srt_bi_full, kind="srt")

    if force_asr:
        # In force_asr mode, we need ASR-based tracks to exist
        srt_es_asr = paths.layout.srt_es_asr_file(base)
        srt_en_asr = paths.layout.srt_en_asr_file(base)
        srt_ru_asr = paths.layout.srt_ru_asr_file(base)
        srt_bi_asr = paths.layout.srt_refs_asr_file(base)
        srt_bi_full_asr = paths.layout.srt_bi_full_asr_file(base)
        _remove_if_empty(srt_es_asr, kind="srt")
        _remove_if_empty(srt_en_asr, kind="srt")
        _remove_if_empty(srt_ru_asr, kind="srt")
        _remove_if_empty(srt_bi_asr, kind="srt")
        _remove_if_empty(srt_bi_full_asr, kind="srt")

        # Require ASR tracks to exist
        if not _is_nonempty_file(srt_es_asr):
            return None
        if not _is_nonempty_file(srt_en_asr):
            return None
        if not _is_nonempty_file(srt_ru_asr) or not _is_nonempty_file(srt_bi_asr) or not _is_nonempty_file(srt_bi_full_asr):
            return None

        subs: list[tuple[Path, str, str]] = []
        # RTVE ES if available
        if _is_nonempty_file(srt_es):
            subs.append((srt_es, "spa", es_model_name))
        # RTVE EN if available
        if _is_nonempty_file(srt_en):
            # Check if this looks like RTVE or MT
            subs.append((srt_en, "eng", "RTVE"))
        # Cached RTVE-based translations
        if _is_nonempty_file(srt_ru) and _is_nonempty_file(srt_bi) and _is_nonempty_file(srt_bi_full):
            subs.extend([
                (srt_ru, "rus", f"{primary_model} MT"),
                (srt_bi, "spa", "ES+RU refs"),
                (srt_bi_full, "spa", "ES+RU"),
            ])
        # ASR tracks (model name unknown in pre-resolve, use generic)
        subs.append((srt_es_asr, "spa", "ASR"))
        subs.extend([
            (srt_en_asr, "eng", f"{primary_model} MT/ASR"),
            (srt_ru_asr, "rus", f"{primary_model} MT/ASR"),
            (srt_bi_asr, "spa", "ES+RU refs/ASR"),
            (srt_bi_full_asr, "spa", "ES+RU/ASR"),
        ])
        return subs, "ES+RU refs/ASR"

    # Normal mode
    if not _is_nonempty_file(srt_es):
        return None

    subs = [(srt_es, "spa", es_model_name)]
    if translate_en_if_missing:
        if not _is_nonempty_file(srt_en):
            return None
        # For pre-resolved path we don't know EN source, use generic MT label
        subs.append((srt_en, "eng", f"{primary_model} MT"))
    if with_ru:
        if not _is_nonempty_file(srt_ru) or not _is_nonempty_file(srt_bi) or not _is_nonempty_file(srt_bi_full):
            return None
        subs.extend(
            [
                (srt_ru, "rus", f"{primary_model} MT"),
                (srt_bi, "spa", "ES+RU refs"),
                (srt_bi_full, "spa", "ES+RU"),
            ]
        )
    return subs, "ES+RU refs"


def _srt_duration_seconds(srt_path: Path) -> float:
    cues = parse_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
    if not cues:
        return 0.0
    max_end_ms = max(c.end_ms for c in cues)
    return max(0.0, max_end_ms / 1000.0)


def _mp4_matches_es_srt(mp4_path: Path, srt_es_path: Path, *, min_ratio: float = 0.70) -> bool:
    v_dur = probe_duration_seconds(mp4_path)
    if v_dur is None:
        return False
    s_dur = _srt_duration_seconds(srt_es_path)
    if s_dur <= 0:
        return True
    return v_dur >= (s_dur * min_ratio)


def _compose_ref_text(es_text: str, ru_refs: str) -> str:
    es = (es_text or "").strip()
    refs = (ru_refs or "").strip()
    if not refs:
        return es
    return f"{es}\n({refs})" if es else f"({refs})"


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
    force_asr: bool = False,
    es_postprocess: bool,
    es_postprocess_force: bool,
    es_postprocess_model: str | None,
    es_postprocess_chunk_cues: int | None,
    asr_model: str,
    asr_device: str,
    asr_compute_type: str,
    asr_batch_size: int,
    asr_vad_method: str,
    asr_backend: str,
    asr_mlx_model: str,
    translation_backend: str = "claude",
    claude_model: str | None = None,
    codex_model: str | None = None,
    codex_chunk_cues: int = 500,
    subtitle_delay_ms: int = 800,
    subtitle_delay_mode: str = "manual",
    subtitle_delay_auto_scope: str = "series",
    subtitle_delay_auto_samples: int = 3,
    subtitle_delay_auto_max_ms: int = 15000,
    subtitle_delay_auto_refresh: bool = False,
    parallel: bool = True,
    jobs_episodes: int = 2,
    jobs_codex_chunks: int = 4,
    reset_layers: list[str] | None = None,
) -> int:
    http = HttpClient()
    paths = _paths_for(series_url, series_slug)
    _ensure_dirs(paths)

    # Model resolution based on backend
    if translation_backend == "claude":
        primary_model = claude_model or "sonnet"
        fallback_model = "opus" if primary_model in ("sonnet", "claude-sonnet-4-20250514") else None
    else:
        primary_model = codex_model or "gpt-5.1-codex-mini"
        fallback_model = "gpt-5.3-codex" if primary_model == "gpt-5.1-codex-mini" else None

    # For ES cleanup, use same backend but potentially different model
    es_clean_default_model = "sonnet" if translation_backend == "claude" else "gpt-5.1-codex-mini"
    global_cache: GlobalPhraseCache = load_global_phrase_cache(Path("data") / "global_phrase_cache.json")
    telemetry = TelemetryDB(paths.layout.telemetry_db())
    run_id = telemetry.start_run(
        slug=paths.slug,
        selector=selector,
        cli_args={
            "series_url": series_url,
            "selector": selector,
            "series_slug": series_slug,
            "parallel": parallel,
            "translation_backend": translation_backend,
            "primary_model": primary_model,
            "codex_chunk_cues": codex_chunk_cues,
            "jobs_codex_chunks": jobs_codex_chunks,
        },
        app_version="0.2.x",
    )
    user_reset_layers = _normalize_reset_layers(reset_layers)
    active_reset_layers = _expand_reset_layers(user_reset_layers)
    if active_reset_layers:
        debug(f"active reset layers: {', '.join(sorted(active_reset_layers))}")
    if "catalog" in active_reset_layers:
        _reset_catalog_layer(paths)
    with stage("catalog"):
        assets = list_assets_for_selector(series_url, selector, http=http, cache_dir=paths.layout.meta)
    _reset_selector_layers(paths=paths, assets=assets, layers={x for x in active_reset_layers if x != "catalog"})

    effective_subtitle_delay_ms = subtitle_delay_ms
    if subtitle_delay_mode == "auto":
        with stage("subtitle-delay:auto"):
            effective_subtitle_delay_ms = estimate_series_delay_ms(
                assets=assets,
                mp4_dir=paths.layout.mp4,
                srt_dir=paths.layout.srt,
                cache_dir=paths.layout.meta,
                out_dir=paths.out,
                scope=subtitle_delay_auto_scope,
                samples=max(1, subtitle_delay_auto_samples),
                max_ms=max(1, subtitle_delay_auto_max_ms),
                refresh=subtitle_delay_auto_refresh,
                asr_backend=asr_backend,
                asr_model=asr_model,
                asr_device=asr_device,
                asr_compute_type=asr_compute_type,
                asr_batch_size=asr_batch_size,
                asr_vad_method=asr_vad_method,
                asr_mlx_model=asr_mlx_model,
            )
        debug(f"subtitle delay selected: {effective_subtitle_delay_ms}ms (mode=auto)")
    else:
        debug(f"subtitle delay selected: {effective_subtitle_delay_ms}ms (mode=manual)")

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
        title_guess = a.title or a.asset_id
        season_num = a.season or 0
        episode_num = a.episode or 0
        base_guess = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title_guess)}"
        ep_tag = base_guess
        telemetry.start_episode(run_id=run_id, episode_id=a.asset_id, base_name=base_guess)
        try:
            _ep_log(ep_tag, "start")
            base = base_guess
            out_mkv = paths.out / f"{base}.mkv"
            mp4_path = paths.layout.mp4_file(base)
            srt_es = paths.layout.srt_es_file(base)
            srt_es_raw = paths.layout.srt / f"{base}.spa.asr_raw.srt"
            _remove_if_empty(out_mkv, kind="mkv")
            if out_mkv.exists():
                debug(f"skip mkv exists (pre-resolve): {out_mkv}")
                _ep_log(ep_tag, "done (cached mkv)")
                telemetry.end_episode(run_id=run_id, episode_id=a.asset_id, status="ok")
                print(out_mkv)
                return None

            # Strict local precheck: if all required mux inputs already exist, skip resolve.
            local_result = _collect_local_subs_for_mux(
                base=base,
                paths=paths,
                with_ru=with_ru,
                translate_en_if_missing=translate_en_if_missing,
                primary_model=primary_model,
                force_asr=force_asr,
            )
            if local_result is not None and is_valid_mp4(mp4_path) and _mp4_matches_es_srt(mp4_path, srt_es):
                local_subs, local_default_title = local_result
                debug(f"local inputs ready (pre-resolve), skipping resolve: {base}")
                _ep_log(ep_tag, "mux")
                with stage(f"mux:{a.asset_id}"):
                    tmp_out = Path(str(out_mkv) + ".partial.mkv")
                    mux_mkv(
                        video_path=mp4_path,
                        out_mkv=tmp_out,
                        subs=local_subs,
                        subtitle_delay_ms=effective_subtitle_delay_ms,
                        default_subtitle_title=local_default_title,
                    )
                    tmp_out.replace(out_mkv)
                _ep_log(ep_tag, f"done ({time.time() - t0:.1f}s)")
                telemetry.end_episode(run_id=run_id, episode_id=a.asset_id, status="ok")
                print(out_mkv)
                return None

            _ep_log(ep_tag, "resolve")
            with stage(f"resolve:{a.asset_id}"):
                resolved = resolver.resolve(a.asset_id, ignore_drm=True)

            title = a.title or resolved.title or a.asset_id
            base = f"S{season_num:02d}E{episode_num:02d}_{_slug_title(title)}"
            ep_tag = base
            _ep_log(ep_tag, "resolved")

            out_mkv = paths.out / f"{base}.mkv"
            mp4_path = paths.layout.mp4_file(base)
            srt_es = paths.layout.srt_es_file(base)
            _remove_if_empty(out_mkv, kind="mkv")
            if out_mkv.exists():
                debug(f"skip mkv exists: {out_mkv}")
                _ep_log(ep_tag, "done (cached mkv)")
                telemetry.end_episode(run_id=run_id, episode_id=a.asset_id, status="ok")
                print(out_mkv)
                return None

            # Video + ES subtitles are independent, so run in parallel.
            def _task_video(force_redownload: bool = False) -> None:
                video_url = _pick_video_url(resolved.video_urls, quality)
                if force_redownload and mp4_path.exists():
                    error(f"{a.asset_id}: forcing mp4 re-download: {mp4_path}")
                    try:
                        mp4_path.unlink()
                    except OSError:
                        pass
                if mp4_path.exists():
                    if is_valid_mp4(mp4_path):
                        debug(f"cache hit mp4: {mp4_path}")
                        return
                    error(f"{a.asset_id}: cached mp4 is invalid, re-downloading: {mp4_path}")
                    try:
                        mp4_path.unlink()
                    except OSError:
                        pass

                with stage(f"download:video:{a.asset_id}"):
                    headers = {"Referer": "https://www.rtve.es/"}
                    download_to_mp4(video_url, mp4_path, headers=headers)
                if not is_valid_mp4(mp4_path):
                    raise RuntimeError(f"downloaded mp4 is invalid: {mp4_path}")

            def _run_es_postprocess(*, es_cues_local: list, source: str) -> list:
                should_run = es_postprocess and (source == "asr" or es_postprocess_force)
                if not should_run:
                    return es_cues_local
                with stage(f"build:srt:es_clean:{a.asset_id}"):
                    _remove_if_empty(srt_es, kind="srt")
                    if _is_nonempty_file(srt_es) and source != "asr":
                        # In force mode with RTVE source we still rebuild from cues.
                        pass
                    cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(es_cues_local) if (c.text or "").strip()]
                    if not cue_tasks:
                        return es_cues_local
                    es_clean_chunk_size = max(1, es_postprocess_chunk_cues)
                    clean_map: dict[str, str] = {}
                    try:
                        clean_map = clean_es_with_codex(
                            cues=cue_tasks,
                            base_path=paths.layout.codex_base(base, "es_clean"),
                            chunk_size_cues=es_clean_chunk_size,
                            model=es_postprocess_model or es_clean_default_model,
                            fallback_model=None,
                            resume=True,
                            max_workers=jobs_codex_chunks,
                            context=CodexExecutionContext(
                                telemetry=telemetry,
                                run_id=run_id,
                                episode_id=a.asset_id,
                                track_type="es_clean",
                                chunk_size=es_clean_chunk_size,
                            ),
                            backend=translation_backend,
                        )
                    except Exception as e:
                        error(f"{a.asset_id}: ES cleanup failed, fallback to raw ES subtitles: {e}")
                        return es_cues_local

                    from rtve_dl.subs.vtt import Cue

                    clean_cues = [
                        Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=clean_map.get(f"{i}", (c.text or "").strip()))
                        for i, c in enumerate(es_cues_local)
                    ]
                    srt_es.write_text(cues_to_srt(clean_cues), encoding="utf-8")
                    return clean_cues

            def _task_es() -> tuple[list, str, str]:
                """Returns (cues, source, model_name) for ES subtitles."""
                if resolved.subtitles_es_vtt:
                    with stage(f"download:subs:es:{a.asset_id}"):
                        es_vtt = paths.layout.vtt_es_file(a.asset_id)
                        _download_sub_vtt(http, resolved.subtitles_es_vtt, es_vtt)
                    with stage(f"build:srt:es:{a.asset_id}"):
                        _remove_if_empty(srt_es, kind="srt")
                        es_cues_local = parse_vtt(es_vtt.read_text(encoding="utf-8"))
                        if not _is_nonempty_file(srt_es):
                            srt_es.write_text(cues_to_srt(es_cues_local), encoding="utf-8")
                        else:
                            debug(f"cache hit srt: {srt_es}")
                    es_cues_local = _run_es_postprocess(es_cues_local=es_cues_local, source="rtve")
                    return es_cues_local, "rtve", "RTVE"

                if not asr_if_missing:
                    raise RuntimeError(
                        f"missing Spanish subtitles for asset {a.asset_id}; enable fallback with --asr-if-missing"
                    )
                with stage(f"build:srt:es_asr:{a.asset_id}"):
                    _remove_if_empty(srt_es_raw, kind="srt")
                    _remove_if_empty(srt_es, kind="srt")
                    if not _is_nonempty_file(srt_es):
                        def _write_canonical_from_raw() -> None:
                            if not _is_nonempty_file(srt_es_raw):
                                raise RuntimeError(f"missing raw ASR srt: {srt_es_raw}")
                            srt_es.write_text(srt_es_raw.read_text(encoding="utf-8"), encoding="utf-8")

                        def _run_asr() -> None:
                            if asr_backend == "mlx":
                                transcribe_es_to_srt_with_mlx_whisper(
                                    media_path=mp4_path,
                                    out_srt=srt_es_raw,
                                    model_repo=asr_mlx_model,
                                )
                            elif asr_backend == "whisperx":
                                transcribe_es_to_srt_with_whisperx(
                                    media_path=mp4_path,
                                    out_srt=srt_es_raw,
                                    model=asr_model,
                                    device=asr_device,
                                    compute_type=asr_compute_type,
                                    batch_size=asr_batch_size,
                                    vad_method=asr_vad_method,
                                )
                            else:
                                raise RuntimeError(f"unsupported ASR backend: {asr_backend}")

                        if _is_nonempty_file(srt_es_raw):
                            debug(f"{a.asset_id}: reusing cached raw ASR subtitles: {srt_es_raw}")
                        else:
                            try:
                                _run_asr()
                            except Exception as e:
                                msg = str(e).lower()
                                recoverable = (
                                    "failed to load audio" in msg
                                    or "invalid data found when processing input" in msg
                                    or "no such file or directory" in msg
                                )
                                if not recoverable:
                                    raise
                                error(f"{a.asset_id}: ASR failed, retrying after forced mp4 re-download")
                                _task_video(force_redownload=True)
                                _run_asr()
                        _write_canonical_from_raw()
                    else:
                        debug(f"cache hit srt: {srt_es}")
                        if not _is_nonempty_file(srt_es_raw):
                            try:
                                srt_es_raw.write_text(srt_es.read_text(encoding="utf-8"), encoding="utf-8")
                            except OSError:
                                pass
                    es_cues_local = parse_srt(srt_es.read_text(encoding="utf-8"))
                    es_cues_local = _run_es_postprocess(es_cues_local=es_cues_local, source="asr")
                    # Build ASR model name for track title
                    if asr_backend == "mlx":
                        asr_model_name = asr_mlx_model.split("/")[-1]  # e.g. "whisper-small-mlx"
                    else:
                        asr_model_name = f"whisperx-{asr_model}"  # e.g. "whisperx-large-v3"
                    return es_cues_local, "asr", asr_model_name

            def _ensure_mp4_consistent_with_es() -> None:
                if not _is_nonempty_file(srt_es):
                    return
                if _mp4_matches_es_srt(mp4_path, srt_es):
                    return
                v_dur = probe_duration_seconds(mp4_path) or 0.0
                s_dur = _srt_duration_seconds(srt_es)
                error(
                    f"{a.asset_id}: mp4 too short vs ES subtitles "
                    f"(video={v_dur:.2f}s subtitles={s_dur:.2f}s), re-downloading"
                )
                _task_video(force_redownload=True)
                if not _mp4_matches_es_srt(mp4_path, srt_es):
                    v_dur2 = probe_duration_seconds(mp4_path) or 0.0
                    raise RuntimeError(
                        f"mp4 duration is still too short after re-download "
                        f"(video={v_dur2:.2f}s subtitles={s_dur:.2f}s)"
                    )

            def _task_asr() -> tuple[list, str]:
                """Always run ASR and return (cues, model_name). Used in force-asr mode."""
                srt_es_asr = paths.layout.srt_es_asr_file(base)
                srt_es_asr_raw = paths.layout.srt / f"{base}.spa.asr_raw.srt"
                with stage(f"build:srt:es_asr_forced:{a.asset_id}"):
                    _remove_if_empty(srt_es_asr_raw, kind="srt")
                    _remove_if_empty(srt_es_asr, kind="srt")
                    if not _is_nonempty_file(srt_es_asr):
                        def _write_asr_from_raw() -> None:
                            if not _is_nonempty_file(srt_es_asr_raw):
                                raise RuntimeError(f"missing raw ASR srt: {srt_es_asr_raw}")
                            srt_es_asr.write_text(srt_es_asr_raw.read_text(encoding="utf-8"), encoding="utf-8")

                        def _run_asr_forced() -> None:
                            if asr_backend == "mlx":
                                transcribe_es_to_srt_with_mlx_whisper(
                                    media_path=mp4_path,
                                    out_srt=srt_es_asr_raw,
                                    model_repo=asr_mlx_model,
                                )
                            elif asr_backend == "whisperx":
                                transcribe_es_to_srt_with_whisperx(
                                    media_path=mp4_path,
                                    out_srt=srt_es_asr_raw,
                                    model=asr_model,
                                    device=asr_device,
                                    compute_type=asr_compute_type,
                                    batch_size=asr_batch_size,
                                    vad_method=asr_vad_method,
                                )
                            else:
                                raise RuntimeError(f"unsupported ASR backend: {asr_backend}")

                        if _is_nonempty_file(srt_es_asr_raw):
                            debug(f"{a.asset_id}: reusing cached raw ASR subtitles: {srt_es_asr_raw}")
                        else:
                            try:
                                _run_asr_forced()
                            except Exception as e:
                                msg = str(e).lower()
                                recoverable = (
                                    "failed to load audio" in msg
                                    or "invalid data found when processing input" in msg
                                    or "no such file or directory" in msg
                                )
                                if not recoverable:
                                    raise
                                error(f"{a.asset_id}: ASR failed, retrying after forced mp4 re-download")
                                _task_video(force_redownload=True)
                                _run_asr_forced()
                        _write_asr_from_raw()
                    else:
                        debug(f"cache hit srt (asr): {srt_es_asr}")

                    asr_cues = parse_srt(srt_es_asr.read_text(encoding="utf-8"))
                    # Build ASR model name for track title
                    if asr_backend == "mlx":
                        asr_model_name_local = asr_mlx_model.split("/")[-1]
                    else:
                        asr_model_name_local = f"whisperx-{asr_model}"
                    return asr_cues, asr_model_name_local

            def _task_en_asr(asr_cues_input: list) -> tuple[Path, str, str]:
                """Translate ASR cues to EN using ASR-specific cache paths."""
                with stage(f"build:srt:en_mt_asr:{a.asset_id}"):
                    srt_en_asr = paths.layout.srt_en_asr_file(base)
                    _remove_if_empty(srt_en_asr, kind="srt")
                    if not _is_nonempty_file(srt_en_asr):
                        cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(asr_cues_input) if (c.text or "").strip()]
                        base_path = paths.layout.codex_base(base, "en_asr")
                        en_map: dict[str, str] = {}
                        if cue_tasks:
                            en_map = translate_es_to_en_with_codex(
                                cues=cue_tasks,
                                base_path=base_path,
                                chunk_size_cues=codex_chunk_cues,
                                model=primary_model,
                                fallback_model=fallback_model,
                                resume=True,
                                max_workers=jobs_codex_chunks,
                                context=CodexExecutionContext(
                                    telemetry=telemetry,
                                    run_id=run_id,
                                    episode_id=a.asset_id,
                                    track_type="en_mt_asr",
                                    chunk_size=codex_chunk_cues,
                                ),
                                backend=translation_backend,
                            )

                        from rtve_dl.subs.vtt import Cue

                        en_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=en_map.get(f"{i}", ""))
                            for i, c in enumerate(asr_cues_input)
                        ]
                        srt_en_asr.write_text(cues_to_srt(en_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt (en_asr): {srt_en_asr}")
                    return (srt_en_asr, "eng", f"{primary_model} MT/ASR")

            def _task_ru_asr(asr_cues_input: list) -> list[tuple[Path, str, str]]:
                """Translate ASR cues to RU using ASR-specific cache paths."""
                with stage(f"build:srt:ru_asr:{a.asset_id}"):
                    srt_ru_asr = paths.layout.srt_ru_asr_file(base)
                    srt_bi_asr = paths.layout.srt_refs_asr_file(base)
                    srt_bi_full_asr = paths.layout.srt_bi_full_asr_file(base)
                    _remove_if_empty(srt_ru_asr, kind="srt")
                    _remove_if_empty(srt_bi_asr, kind="srt")
                    _remove_if_empty(srt_bi_full_asr, kind="srt")
                    cue_tasks = [
                        (f"{i}", (c.text or "").strip())
                        for i, c in enumerate(asr_cues_input)
                        if (c.text or "").strip()
                    ]
                    ru_map: dict[str, str] | None = None

                    if not _is_nonempty_file(srt_ru_asr):
                        base_path = paths.layout.codex_base(base, "ru_asr")
                        ru_map = {}
                        if cue_tasks:
                            ru_map = translate_es_to_ru_with_codex(
                                cues=cue_tasks,
                                base_path=base_path,
                                chunk_size_cues=codex_chunk_cues,
                                model=primary_model,
                                fallback_model=fallback_model,
                                resume=True,
                                max_workers=jobs_codex_chunks,
                                context=CodexExecutionContext(
                                    telemetry=telemetry,
                                    run_id=run_id,
                                    episode_id=a.asset_id,
                                    track_type="ru_full_asr",
                                    chunk_size=codex_chunk_cues,
                                ),
                                backend=translation_backend,
                            )

                        from rtve_dl.subs.vtt import Cue

                        ru_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru_map.get(f"{i}", ""))
                            for i, c in enumerate(asr_cues_input)
                        ]
                        srt_ru_asr.write_text(cues_to_srt(ru_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt (ru_asr): {srt_ru_asr}")

                    if not _is_nonempty_file(srt_bi_asr):
                        refs_base_path = paths.layout.codex_base(base, "ru_ref_asr")
                        refs_map: dict[str, str] = {}
                        if cue_tasks:
                            refs_chunk_size = min(400, codex_chunk_cues)
                            refs_workers = max(1, min(2, jobs_codex_chunks))
                            refs_map = translate_es_to_ru_refs_with_codex(
                                cues=cue_tasks,
                                base_path=refs_base_path,
                                chunk_size_cues=refs_chunk_size,
                                model=primary_model,
                                fallback_model=fallback_model,
                                resume=True,
                                max_workers=refs_workers,
                                context=CodexExecutionContext(
                                    telemetry=telemetry,
                                    run_id=run_id,
                                    episode_id=a.asset_id,
                                    track_type="ru_refs_asr",
                                    chunk_size=refs_chunk_size,
                                ),
                                backend=translation_backend,
                            )

                        from rtve_dl.subs.vtt import Cue

                        ref_cues = [
                            Cue(
                                start_ms=c.start_ms,
                                end_ms=c.end_ms,
                                text=_compose_ref_text((c.text or "").strip(), refs_map.get(f"{i}", "")),
                            )
                            for i, c in enumerate(asr_cues_input)
                        ]
                        srt_bi_asr.write_text(cues_to_srt(ref_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt (refs_asr): {srt_bi_asr}")

                    if not _is_nonempty_file(srt_bi_full_asr):
                        if ru_map is None:
                            ru_cues_cached = parse_srt(srt_ru_asr.read_text(encoding="utf-8"))
                            ru_map = {f"{i}": (c.text or "").strip() for i, c in enumerate(ru_cues_cached)}

                        from rtve_dl.subs.vtt import Cue

                        bi_full_cues = [
                            Cue(
                                start_ms=c.start_ms,
                                end_ms=c.end_ms,
                                text=((c.text or "").strip() + "\n" + (ru_map.get(f"{i}", "") or "").strip()).strip(),
                            )
                            for i, c in enumerate(asr_cues_input)
                        ]
                        srt_bi_full_asr.write_text(cues_to_srt(bi_full_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt (bi_full_asr): {srt_bi_full_asr}")

                    return [
                        (srt_ru_asr, "rus", f"{primary_model} MT/ASR"),
                        (srt_bi_asr, "spa", "ES+RU refs/ASR"),
                        (srt_bi_full_asr, "spa", "ES+RU/ASR"),
                    ]

            # ES track title will be set after _task_es() returns the model name
            subs: list[tuple[Path, str, str]] = []

            def _task_en() -> tuple[Path, str, str] | None:
                if resolved.subtitles_en_vtt:
                    with stage(f"download:subs:en:{a.asset_id}"):
                        _download_sub_vtt(http, resolved.subtitles_en_vtt, paths.layout.vtt_en_file(a.asset_id))
                    with stage(f"build:srt:en:{a.asset_id}"):
                        en_vtt = paths.layout.vtt_en_file(a.asset_id)
                        en_cues = parse_vtt(en_vtt.read_text(encoding="utf-8"))
                        srt_en = paths.layout.srt_en_file(base)
                        _remove_if_empty(srt_en, kind="srt")
                        if not _is_nonempty_file(srt_en):
                            srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                        else:
                            debug(f"cache hit srt: {srt_en}")
                        return (srt_en, "eng", "RTVE")

                if not translate_en_if_missing:
                    return None

                # Fallback: machine-translate ES -> EN using translation backend chunks.
                with stage(f"build:srt:en_mt:{a.asset_id}"):
                    srt_en = paths.layout.srt_en_file(base)
                    _remove_if_empty(srt_en, kind="srt")
                    if not _is_nonempty_file(srt_en):
                        cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(es_cues) if (c.text or "").strip()]
                        en_cached, en_missing = global_cache.split_for_track(cues=cue_tasks, track="en_mt")
                        base_path = paths.layout.codex_base(base, "en")
                        en_map = dict(en_cached)
                        if en_missing:
                            en_map.update(
                                translate_es_to_en_with_codex(
                                    cues=en_missing,
                                    base_path=base_path,
                                    chunk_size_cues=codex_chunk_cues,
                                    model=primary_model,
                                    fallback_model=fallback_model,
                                    resume=True,
                                    max_workers=jobs_codex_chunks,
                                    context=CodexExecutionContext(
                                        telemetry=telemetry,
                                        run_id=run_id,
                                        episode_id=a.asset_id,
                                        track_type="en_mt",
                                        chunk_size=codex_chunk_cues,
                                    ),
                                    backend=translation_backend,
                                )
                            )

                        from rtve_dl.subs.vtt import Cue

                        en_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=en_map.get(f"{i}", ""))
                            for i, c in enumerate(es_cues)
                        ]
                        srt_en.write_text(cues_to_srt(en_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_en}")
                    return (srt_en, "eng", f"{primary_model} MT")

            def _task_ru() -> list[tuple[Path, str, str]]:
                if not with_ru:
                    if require_ru:
                        raise RuntimeError("RU subtitles are required but disabled (--no-with-ru)")
                    return []
                with stage(f"build:srt:ru:{a.asset_id}"):
                    srt_ru = paths.layout.srt_ru_file(base)
                    srt_bi = paths.layout.srt_refs_file(base)
                    srt_bi_full = paths.layout.srt_bi_full_file(base)
                    _remove_if_empty(srt_ru, kind="srt")
                    _remove_if_empty(srt_bi, kind="srt")
                    _remove_if_empty(srt_bi_full, kind="srt")
                    cue_tasks = [
                        (f"{i}", (c.text or "").strip())
                        for i, c in enumerate(es_cues)
                        if (c.text or "").strip()
                    ]
                    ru_map: dict[str, str] | None = None

                    if not _is_nonempty_file(srt_ru):
                        ru_cached, ru_missing = global_cache.split_for_track(cues=cue_tasks, track="ru_full")
                        base_path = paths.layout.codex_base(base, "ru")
                        ru_map = dict(ru_cached)
                        if ru_missing:
                            ru_map.update(
                                translate_es_to_ru_with_codex(
                                    cues=ru_missing,
                                    base_path=base_path,
                                    chunk_size_cues=codex_chunk_cues,
                                    model=primary_model,
                                    fallback_model=fallback_model,
                                    resume=True,
                                    max_workers=jobs_codex_chunks,
                                    context=CodexExecutionContext(
                                        telemetry=telemetry,
                                        run_id=run_id,
                                        episode_id=a.asset_id,
                                        track_type="ru_full",
                                        chunk_size=codex_chunk_cues,
                                    ),
                                    backend=translation_backend,
                                )
                            )

                        from rtve_dl.subs.vtt import Cue

                        ru_cues = [
                            Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=ru_map.get(f"{i}", ""))
                            for i, c in enumerate(es_cues)
                        ]
                        srt_ru.write_text(cues_to_srt(ru_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_ru}")

                    if not _is_nonempty_file(srt_bi):
                        refs_cached, refs_missing = global_cache.split_for_track(cues=cue_tasks, track="ru_refs")
                        refs_base_path = paths.layout.codex_base(base, "ru_ref")
                        refs_map = dict(refs_cached)
                        if refs_missing:
                            refs_chunk_size = min(400, codex_chunk_cues)
                            refs_workers = max(1, min(2, jobs_codex_chunks))
                            refs_map.update(
                                translate_es_to_ru_refs_with_codex(
                                    cues=refs_missing,
                                    base_path=refs_base_path,
                                    chunk_size_cues=refs_chunk_size,
                                    model=primary_model,
                                    fallback_model=fallback_model,
                                    resume=True,
                                    max_workers=refs_workers,
                                    context=CodexExecutionContext(
                                        telemetry=telemetry,
                                        run_id=run_id,
                                        episode_id=a.asset_id,
                                        track_type="ru_refs",
                                        chunk_size=refs_chunk_size,
                                    ),
                                    backend=translation_backend,
                                )
                            )

                        from rtve_dl.subs.vtt import Cue

                        ref_cues = [
                            Cue(
                                start_ms=c.start_ms,
                                end_ms=c.end_ms,
                                text=_compose_ref_text((c.text or "").strip(), refs_map.get(f"{i}", "")),
                            )
                            for i, c in enumerate(es_cues)
                        ]
                        srt_bi.write_text(cues_to_srt(ref_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_bi}")

                    if not _is_nonempty_file(srt_bi_full):
                        if ru_map is None:
                            ru_cues_cached = parse_srt(srt_ru.read_text(encoding="utf-8"))
                            ru_map = {f"{i}": (c.text or "").strip() for i, c in enumerate(ru_cues_cached)}

                        from rtve_dl.subs.vtt import Cue

                        bi_full_cues = [
                            Cue(
                                start_ms=c.start_ms,
                                end_ms=c.end_ms,
                                text=((c.text or "").strip() + "\n" + (ru_map.get(f"{i}", "") or "").strip()).strip(),
                            )
                            for i, c in enumerate(es_cues)
                        ]
                        srt_bi_full.write_text(cues_to_srt(bi_full_cues), encoding="utf-8")
                    else:
                        debug(f"cache hit srt: {srt_bi_full}")

                    return [
                        (srt_ru, "rus", f"{primary_model} MT"),
                        (srt_bi, "spa", "ES+RU refs"),
                        (srt_bi_full, "spa", "ES+RU"),
                    ]

            _ep_log(ep_tag, "video+es")
            default_subtitle_title = "ES+RU refs"

            if force_asr:
                # Force-ASR mode: always run ASR, skip generating RTVE-based translations
                # but include cached RTVE translations if they exist.
                _task_video()
                _ensure_mp4_consistent_with_es()

                # Get RTVE ES subtitles if available (for reference track)
                rtve_es_available = resolved.subtitles_es_vtt is not None
                if rtve_es_available:
                    es_cues, _es_source, es_model_name = _task_es()
                    subs.append((srt_es, "spa", es_model_name))

                # RTVE EN if available
                if resolved.subtitles_en_vtt:
                    with stage(f"download:subs:en:{a.asset_id}"):
                        _download_sub_vtt(http, resolved.subtitles_en_vtt, paths.layout.vtt_en_file(a.asset_id))
                    with stage(f"build:srt:en:{a.asset_id}"):
                        en_vtt = paths.layout.vtt_en_file(a.asset_id)
                        en_cues = parse_vtt(en_vtt.read_text(encoding="utf-8"))
                        srt_en_rtve = paths.layout.srt_en_file(base)
                        _remove_if_empty(srt_en_rtve, kind="srt")
                        if not _is_nonempty_file(srt_en_rtve):
                            srt_en_rtve.write_text(cues_to_srt(en_cues), encoding="utf-8")
                        else:
                            debug(f"cache hit srt: {srt_en_rtve}")
                        subs.append((srt_en_rtve, "eng", "RTVE"))

                # Check for cached RTVE-based translations (don't regenerate, just include if exist)
                srt_en = paths.layout.srt_en_file(base)
                srt_ru = paths.layout.srt_ru_file(base)
                srt_bi = paths.layout.srt_refs_file(base)
                srt_bi_full = paths.layout.srt_bi_full_file(base)

                # Only include cached EN MT if not already added as RTVE EN
                if not resolved.subtitles_en_vtt and _is_nonempty_file(srt_en):
                    subs.append((srt_en, "eng", f"{primary_model} MT"))
                if _is_nonempty_file(srt_ru) and _is_nonempty_file(srt_bi) and _is_nonempty_file(srt_bi_full):
                    subs.extend([
                        (srt_ru, "rus", f"{primary_model} MT"),
                        (srt_bi, "spa", "ES+RU refs"),
                        (srt_bi_full, "spa", "ES+RU"),
                    ])

                # ASR-based tracks (always generated in force-asr mode)
                _ep_log(ep_tag, "asr+translations")
                asr_cues, asr_model_name = _task_asr()
                srt_es_asr = paths.layout.srt_es_asr_file(base)
                subs.append((srt_es_asr, "spa", asr_model_name))

                if parallel:
                    with ThreadPoolExecutor(max_workers=2) as tr_pool:
                        fut_en_asr = tr_pool.submit(_task_en_asr, asr_cues)
                        fut_ru_asr = tr_pool.submit(_task_ru_asr, asr_cues)
                        try:
                            en_asr_track = fut_en_asr.result()
                            subs.append(en_asr_track)
                        except Exception as e:
                            error(f"{a.asset_id}: EN ASR translation failed (continuing): {e}")
                        ru_asr_tracks = fut_ru_asr.result()
                        subs.extend(ru_asr_tracks)
                else:
                    try:
                        en_asr_track = _task_en_asr(asr_cues)
                        subs.append(en_asr_track)
                    except Exception as e:
                        error(f"{a.asset_id}: EN ASR translation failed (continuing): {e}")
                    subs.extend(_task_ru_asr(asr_cues))

                # Default is ASR-based refs
                default_subtitle_title = "ES+RU refs/ASR"

            elif parallel:
                # Normal mode with parallel execution
                # ES subtitle download can run in parallel with video download.
                # But ASR fallback requires a fully downloaded MP4, so keep that path sequential.
                if resolved.subtitles_es_vtt:
                    with ThreadPoolExecutor(max_workers=1) as video_pool:
                        video_future = video_pool.submit(_task_video)
                        es_cues, _es_source, es_model_name = _task_es()
                        subs.append((srt_es, "spa", es_model_name))
                        video_future.result()
                        _ensure_mp4_consistent_with_es()
                        _ep_log(ep_tag, "translations")
                        with ThreadPoolExecutor(max_workers=2) as tr_pool:
                            fut_ru = tr_pool.submit(_task_ru)
                            fut_en = tr_pool.submit(_task_en)
                            try:
                                en_track = fut_en.result()
                                if en_track is not None:
                                    subs.append(en_track)
                            except Exception as e:
                                # EN fallback errors should not fail episode.
                                error(f"{a.asset_id}: EN subtitle fallback failed (continuing): {e}")
                            ru_tracks = fut_ru.result()
                            subs.extend(ru_tracks)
                else:
                    _task_video()
                    es_cues, _es_source, es_model_name = _task_es()
                    subs.append((srt_es, "spa", es_model_name))
                    _ensure_mp4_consistent_with_es()
                    _ep_log(ep_tag, "translations")
                    with ThreadPoolExecutor(max_workers=2) as tr_pool:
                        fut_ru = tr_pool.submit(_task_ru)
                        fut_en = tr_pool.submit(_task_en)
                        try:
                            en_track = fut_en.result()
                            if en_track is not None:
                                subs.append(en_track)
                        except Exception as e:
                            error(f"{a.asset_id}: EN subtitle fallback failed (continuing): {e}")
                        ru_tracks = fut_ru.result()
                        subs.extend(ru_tracks)
            else:
                # Normal mode without parallel execution
                _task_video()
                es_cues, _es_source, es_model_name = _task_es()
                subs.append((srt_es, "spa", es_model_name))
                _ensure_mp4_consistent_with_es()
                _ep_log(ep_tag, "translations")
                try:
                    en_track = _task_en()
                    if en_track is not None:
                        subs.append(en_track)
                except Exception as e:
                    error(f"{a.asset_id}: EN subtitle fallback failed (continuing): {e}")
                subs.extend(_task_ru())

            _ep_log(ep_tag, "mux")
            with stage(f"mux:{a.asset_id}"):
                tmp_out = Path(str(out_mkv) + ".partial.mkv")
                mux_mkv(
                    video_path=mp4_path,
                    out_mkv=tmp_out,
                    subs=subs,
                    subtitle_delay_ms=effective_subtitle_delay_ms,
                    default_subtitle_title=default_subtitle_title,
                )
                tmp_out.replace(out_mkv)
            _ep_log(ep_tag, f"done ({time.time() - t0:.1f}s)")
            telemetry.end_episode(run_id=run_id, episode_id=a.asset_id, status="ok")
            print(out_mkv)
            return None
        except Exception as e:
            msg = f"{a.asset_id}: {e}"
            _ep_log(ep_tag, f"fail ({time.time() - t0:.1f}s)")
            error(msg)
            telemetry.end_episode(run_id=run_id, episode_id=a.asset_id, status="failed")
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

    try:
        debug(f"index:start {paths.out}")
        index_path = build_slug_index(
            paths.out,
            tmp_dir=paths.layout.meta,
            codex_dir=paths.layout.codex_ru,
            codex_model=primary_model,
            codex_chunk_cues=codex_chunk_cues,
            jobs_codex_chunks=jobs_codex_chunks,
            translation_backend=translation_backend,
        )
        debug(f"index:done {index_path}")
    except Exception as e:
        error(f"index:fail {paths.out}: {e}")
    status = "failed" if failures else "ok"
    telemetry.end_run(run_id=run_id, status=status)
    return 1 if failures else 0
