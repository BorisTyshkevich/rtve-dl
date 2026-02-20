from __future__ import annotations

from pathlib import Path

from rtve_dl.codex_batch import CodexExecutionContext, translate_es


def translate_es_to_ru_refs_with_codex(
    *,
    cues: list[tuple[str, str]],
    base_path: Path,
    chunk_size_cues: int,
    model: str | None,
    fallback_model: str | None,
    resume: bool,
    max_workers: int,
    context: CodexExecutionContext | None = None,
    backend: str = "claude",
    no_chunk: bool | None = None,
) -> dict[str, str]:
    """
    Spanish -> Spanish-with-inline-Russian-glosses (B2/C1/C2 only) via translation backend.
    Refs are cue-local: we intentionally disable neighbor context to avoid
    bleed from previous/next subtitle lines.
    Returns id->annotated_text for all cues provided.
    """
    return translate_es(
        cues=cues,
        base_path=base_path,
        chunk_size_cues=chunk_size_cues,
        model=model,
        fallback_model=fallback_model,
        resume=resume,
        target_language="RussianRefs",
        io_tag="ruref",
        max_workers=max_workers,
        prompt_mode="ru_refs_b2plus",
        context=context,
        use_context=False,
        backend=backend,
        no_chunk=no_chunk,
    )
