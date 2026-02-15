from __future__ import annotations

from pathlib import Path

from rtve_dl.codex_batch import CodexExecutionContext, translate_es_with_codex


def clean_es_with_codex(
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
) -> dict[str, str]:
    """
    Spanish -> Spanish light editorial cleanup via translation backend chunk pipeline.
    Returns id->cleaned_es_text for all cues provided.
    """
    return translate_es_with_codex(
        cues=cues,
        base_path=base_path,
        chunk_size_cues=chunk_size_cues,
        model=model,
        fallback_model=fallback_model,
        resume=resume,
        target_language="SpanishClean",
        io_tag="es_clean",
        max_workers=max_workers,
        prompt_mode="es_clean_light",
        context=context,
        backend=backend,
    )
