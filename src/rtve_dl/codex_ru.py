from __future__ import annotations

from pathlib import Path

from rtve_dl.codex_batch import CodexExecutionContext, translate_es


def translate_es_to_ru_with_codex(
    *,
    cues: list[tuple[str, str]],
    base_path: Path,
    chunk_size_cues: int,
    model: str | None,
    fallback_model: str | None,
    resume: bool,
    max_workers: int,
    context: CodexExecutionContext | None = None,
    use_context: bool = True,
    backend: str = "claude",
    no_chunk: bool | None = None,
) -> dict[str, str]:
    """
    Spanish -> Russian batch translation via translation backend.
    Returns id->ru_text for all cues provided.
    """
    return translate_es(
        cues=cues,
        base_path=base_path,
        chunk_size_cues=chunk_size_cues,
        model=model,
        fallback_model=fallback_model,
        resume=resume,
        target_language="Russian",
        io_tag="ru",
        max_workers=max_workers,
        prompt_mode="translate_ru",
        context=context,
        use_context=use_context,
        backend=backend,
        no_chunk=no_chunk,
    )
