"""Deduplication of repetition hallucinations from ASR output.

Whisper ASR sometimes produces hallucinated repetitions during music/silence segments:
- "League League League..." (28-221 repetitions)
- "Yuk Yuk Yuk..." (221 repetitions)
- "no, no, no, no..." (111 repetitions)

This module detects and removes such repetitions, leaving only 1 instance.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rtve_dl.log import debug

if TYPE_CHECKING:
    from rtve_dl.subs.vtt import Cue


def deduplicate_repetitions(text: str) -> tuple[str, bool]:
    """
    Remove repetitive patterns (4+ consecutive), leaving only 1 instance.

    Returns (cleaned_text, was_modified).

    Examples:
    - "no, no, no, no, no, no" → ("no", True)
    - "League League League League League" → ("League", True)
    - "Yuk Yuk Yuk Yuk..." → ("Yuk", True)
    - "PASME PASME PASME PASME..." → ("PASME", True)
    - "Hello world" → ("Hello world", False)
    """
    modified = False

    def reduce_to_one(m: re.Match[str]) -> str:
        nonlocal modified
        modified = True
        return m.group(1)  # Keep only 1 instance

    # Pattern for comma-separated: "no, no, no, no..." → "no"
    # Matches word followed by 3+ repetitions of (comma + optional space + same word)
    pattern_comma = r"\b(\w+)(,\s*\1){3,}\b"

    # Pattern for space-separated: "League League League..." → "League"
    # Matches word followed by 3+ repetitions of (whitespace + same word)
    pattern_space = r"\b(\w+)(\s+\1){3,}\b"

    # Pattern for multi-char token repeated with space: "PASME PASME PASME..."
    # More aggressive: capture 2+ char sequences repeated 4+ times
    # This handles cases where word boundary doesn't match due to leading fragments
    pattern_token = r"(\S{2,})(\s+\1){3,}"

    text = re.sub(pattern_comma, reduce_to_one, text, flags=re.IGNORECASE)
    text = re.sub(pattern_space, reduce_to_one, text, flags=re.IGNORECASE)
    text = re.sub(pattern_token, reduce_to_one, text, flags=re.IGNORECASE)

    return text, modified


def deduplicate_cue_repetitions(cues: list[Cue]) -> list[Cue]:
    """Apply deduplication to all cues, log when hallucinations detected."""
    from rtve_dl.subs.vtt import Cue

    result: list[Cue] = []
    for cue in cues:
        text = (cue.text or "").strip()
        cleaned, was_modified = deduplicate_repetitions(text)
        if was_modified:
            orig_preview = text[:60] + "..." if len(text) > 60 else text
            clean_preview = cleaned[:60] + "..." if len(cleaned) > 60 else cleaned
            debug(f"dedup: removed repetitions at {cue.start_ms}ms: '{orig_preview}' → '{clean_preview}'")
        result.append(Cue(start_ms=cue.start_ms, end_ms=cue.end_ms, text=cleaned))
    return result


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison (lowercase, strip punctuation at edges)."""
    t = (text or "").strip().lower()
    # Remove leading/trailing punctuation for comparison
    t = t.strip(".,!?¿¡;:\"'")
    return t


def collapse_consecutive_duplicates(
    cues: list[Cue],
    *,
    min_consecutive: int = 4,
) -> list[Cue]:
    """
    Collapse consecutive cues with identical text into a single cue.

    This handles cross-cue repetition hallucinations like:
    - 19 consecutive cues all saying "No, no."
    - 10 consecutive cues all saying "Sí."

    Args:
        cues: List of Cue objects
        min_consecutive: Minimum consecutive duplicates to trigger collapse (default 4)

    Returns:
        List of Cue objects with consecutive duplicates collapsed
    """
    from rtve_dl.subs.vtt import Cue

    if not cues:
        return []

    result: list[Cue] = []
    i = 0

    while i < len(cues):
        current = cues[i]
        current_norm = _normalize_for_comparison(current.text)

        # Count consecutive cues with same normalized text
        run_end = i + 1
        while run_end < len(cues):
            next_norm = _normalize_for_comparison(cues[run_end].text)
            if next_norm != current_norm:
                break
            run_end += 1

        run_length = run_end - i

        if run_length >= min_consecutive and current_norm:
            # Collapse: keep first cue but extend end time to last cue's end
            first_cue = cues[i]
            last_cue = cues[run_end - 1]
            collapsed = Cue(
                start_ms=first_cue.start_ms,
                end_ms=last_cue.end_ms,
                text=first_cue.text,  # Keep original text (not normalized)
            )
            result.append(collapsed)
            debug(
                f"dedup: collapsed {run_length} consecutive '{current_norm}' cues "
                f"({first_cue.start_ms}ms-{last_cue.end_ms}ms)"
            )
            i = run_end
        else:
            # Keep cue as-is
            result.append(current)
            i += 1

    return result


def deduplicate_asr_hallucinations(cues: list[Cue]) -> list[Cue]:
    """
    Full deduplication pipeline for ASR output:
    1. Within-cue repetition removal
    2. Cross-cue consecutive duplicate collapse

    This is the main entry point for ASR hallucination cleanup.
    """
    # Step 1: Within-cue deduplication
    cues = deduplicate_cue_repetitions(cues)
    # Step 2: Cross-cue collapse
    cues = collapse_consecutive_duplicates(cues)
    return cues
