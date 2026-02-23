from __future__ import annotations

from pathlib import Path

from rtve_dl.codex_batch import CodexExecutionContext
from rtve_dl.codex_ru import translate_es_to_ru_with_codex
from rtve_dl.codex_ru_refs import translate_es_to_ru_refs_with_codex
from rtve_dl.global_phrase_cache import GlobalPhraseCache
from rtve_dl.log import debug
from rtve_dl.subtitle_tracks.builders import build_refs_srt, build_ru_dual_srt, build_ru_srt
from rtve_dl.subtitle_tracks.models import TRACK_REFS, TRACK_REFS_ASR, TRACK_RU, TRACK_RU_ASR, TRACK_RU_DUAL, TRACK_RU_DUAL_ASR, ProducedTrack
from rtve_dl.tmp_layout import TmpLayout


def _is_nonempty_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def track_file_specs(*, layout: TmpLayout, base: str, force_asr: bool, primary_model: str) -> dict[str, ProducedTrack]:
    if force_asr:
        return {
            TRACK_RU_ASR: ProducedTrack(TRACK_RU_ASR, layout.srt_ru_asr_file(base), "rus", f"{primary_model} MT/ASR"),
            TRACK_REFS_ASR: ProducedTrack(TRACK_REFS_ASR, layout.srt_refs_asr_file(base), "spa", "ES+RU refs/ASR"),
            TRACK_RU_DUAL_ASR: ProducedTrack(TRACK_RU_DUAL_ASR, layout.srt_bi_full_asr_file(base), "rus", "ES+RU/ASR"),
        }
    return {
        TRACK_RU: ProducedTrack(TRACK_RU, layout.srt_ru_file(base), "rus", f"{primary_model} MT"),
        TRACK_REFS: ProducedTrack(TRACK_REFS, layout.srt_refs_file(base), "spa", "ES+RU refs"),
        TRACK_RU_DUAL: ProducedTrack(TRACK_RU_DUAL, layout.srt_bi_full_file(base), "rus", "ES+RU"),
    }


def build_ru_tracks(
    *,
    cues: list,
    base: str,
    asset_id: str,
    layout: TmpLayout,
    global_cache: GlobalPhraseCache,
    primary_model: str,
    fallback_model: str | None,
    codex_chunk_cues: int,
    jobs_codex_chunks: int,
    translation_backend: str,
    no_chunk: bool | None,
    telemetry,
    run_id: int,
    enabled_track_ids: set[str],
    force_asr: bool,
) -> list[ProducedTrack]:
    if not enabled_track_ids:
        return []
    specs = track_file_specs(layout=layout, base=base, force_asr=force_asr, primary_model=primary_model)
    cue_tasks = [(f"{i}", (c.text or "").strip()) for i, c in enumerate(cues) if (c.text or "").strip()]
    ru_key = TRACK_RU_ASR if force_asr else TRACK_RU
    refs_key = TRACK_REFS_ASR if force_asr else TRACK_REFS
    dual_key = TRACK_RU_DUAL_ASR if force_asr else TRACK_RU_DUAL
    ru_track_type = "ru_full_asr" if force_asr else "ru_full"
    refs_track_type = "ru_refs_asr" if force_asr else "ru_refs"
    ru_cache_track = "ru_full"
    refs_cache_track = "ru_refs"
    ru_codex = layout.codex_base(base, "ru_asr" if force_asr else "ru")
    refs_codex = layout.codex_base(base, "ru_ref_asr" if force_asr else "ru_ref")

    ru_map: dict[str, str] = {}
    if ru_key in enabled_track_ids:
        if _is_nonempty_file(specs[ru_key].path):
            debug(f"ru_full: cache hit srt, skipping translation: {specs[ru_key].path}")
        else:
            ru_cached, ru_missing = global_cache.split_for_track(cues=cue_tasks, track=ru_cache_track)
            ru_map = dict(ru_cached)
            if ru_missing:
                ru_map.update(
                    translate_es_to_ru_with_codex(
                        cues=ru_missing,
                        base_path=ru_codex,
                        chunk_size_cues=codex_chunk_cues,
                        model=primary_model,
                        fallback_model=fallback_model,
                        resume=True,
                        max_workers=jobs_codex_chunks,
                        context=CodexExecutionContext(
                            telemetry=telemetry,
                            run_id=run_id,
                            episode_id=asset_id,
                            track_type=ru_track_type,
                            chunk_size=codex_chunk_cues,
                        ),
                        backend=translation_backend,
                        no_chunk=no_chunk,
                    )
                )
            build_ru_srt(srt_path=specs[ru_key].path, cues=cues, ru_map=ru_map)

    if refs_key in enabled_track_ids:
        refs_cached, refs_missing = global_cache.split_for_track(cues=cue_tasks, track=refs_cache_track)
        refs_map = dict(refs_cached)
        if refs_missing:
            refs_chunk_size = min(400, codex_chunk_cues)
            refs_workers = max(1, min(2, jobs_codex_chunks))
            refs_map.update(
                translate_es_to_ru_refs_with_codex(
                    cues=refs_missing,
                    base_path=refs_codex,
                    chunk_size_cues=refs_chunk_size,
                    model=primary_model,
                    fallback_model=fallback_model,
                    resume=True,
                    max_workers=refs_workers,
                    context=CodexExecutionContext(
                        telemetry=telemetry,
                        run_id=run_id,
                        episode_id=asset_id,
                        track_type=refs_track_type,
                        chunk_size=refs_chunk_size,
                    ),
                    backend=translation_backend,
                    no_chunk=no_chunk,
                )
            )
        build_refs_srt(srt_path=specs[refs_key].path, cues=cues, refs_map=refs_map)

    if dual_key in enabled_track_ids:
        build_ru_dual_srt(
            srt_path=specs[dual_key].path,
            cues=cues,
            ru_map=ru_map,
            ru_srt_fallback=specs[ru_key].path,
        )

    out: list[ProducedTrack] = []
    for track_id in (ru_key, refs_key, dual_key):
        if track_id in enabled_track_ids:
            out.append(specs[track_id])
    return out


def local_track_file_map(*, layout: TmpLayout, base: str, force_asr: bool, primary_model: str) -> dict[str, ProducedTrack]:
    return track_file_specs(layout=layout, base=base, force_asr=force_asr, primary_model=primary_model)
