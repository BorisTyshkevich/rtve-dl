from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rtve_dl.log import debug, stage
from rtve_dl.subs.vtt import Cue

if TYPE_CHECKING:
    import whisperx
    import torch


@dataclass(frozen=True)
class AlignOptions:
    device: str
    align_model: str | None = None


_ALIGN_MAX_ADJUST_MS = 5000


def _require_whisperx() -> None:
    try:
        import whisperx  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "WhisperX is required for subtitle alignment. Install with "
            "`pip install -e '.[asr-whisperx]'`."
        ) from e


def _resolve_device(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in {"auto", "mps", "cpu"}:
        raise RuntimeError(f"unknown subtitle align device: {mode}")
    try:
        import torch
    except Exception as e:
        raise RuntimeError("torch is required for subtitle alignment.") from e

    if mode == "cpu":
        return "cpu"
    if mode == "mps":
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS requested but not available. Use --subtitle-align-device auto or cpu.")
        return "mps"
    # auto
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _extract_word_times(segment: dict) -> tuple[float | None, float | None]:
    words = segment.get("words") if isinstance(segment, dict) else None
    if not isinstance(words, list) or not words:
        return None, None
    starts = [w.get("start") for w in words if isinstance(w, dict) and isinstance(w.get("start"), (int, float))]
    ends = [w.get("end") for w in words if isinstance(w, dict) and isinstance(w.get("end"), (int, float))]
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def retime_cues_from_segments(cues: list[Cue], aligned_segments: list[dict]) -> list[Cue]:
    # Prefer id-based mapping when available to handle segment splits/merges.
    by_id: dict[int, tuple[float, float]] = {}
    for seg in aligned_segments:
        if not isinstance(seg, dict):
            continue
        seg_id = seg.get("id")
        if not isinstance(seg_id, int):
            continue
        start_s, end_s = _extract_word_times(seg)
        if start_s is None or end_s is None or end_s <= start_s:
            continue
        if seg_id in by_id:
            prev_s, prev_e = by_id[seg_id]
            by_id[seg_id] = (min(prev_s, start_s), max(prev_e, end_s))
        else:
            by_id[seg_id] = (start_s, end_s)

    out: list[Cue] = []
    clamped = 0
    if by_id:
        for idx, cue in enumerate(cues):
            if idx not in by_id:
                out.append(cue)
                continue
            start_s, end_s = by_id[idx]
            if end_s <= start_s:
                out.append(cue)
                continue
            start_ms = max(0, int(start_s * 1000))
            end_ms = max(start_ms + 1, int(end_s * 1000))
            if (
                abs(start_ms - cue.start_ms) > _ALIGN_MAX_ADJUST_MS
                or abs(end_ms - cue.end_ms) > _ALIGN_MAX_ADJUST_MS
            ):
                clamped += 1
                out.append(cue)
                continue
            out.append(Cue(start_ms=start_ms, end_ms=end_ms, text=cue.text))
        if clamped:
            debug(f"whisperx alignment clamped {clamped} cue(s) beyond {_ALIGN_MAX_ADJUST_MS}ms")
        return out

    for cue, seg in zip(cues, aligned_segments):
        start_s, end_s = _extract_word_times(seg)
        if start_s is None or end_s is None or end_s <= start_s:
            out.append(cue)
            continue
        start_ms = max(0, int(start_s * 1000))
        end_ms = max(start_ms + 1, int(end_s * 1000))
        if (
            abs(start_ms - cue.start_ms) > _ALIGN_MAX_ADJUST_MS
            or abs(end_ms - cue.end_ms) > _ALIGN_MAX_ADJUST_MS
        ):
            clamped += 1
            out.append(cue)
            continue
        out.append(Cue(start_ms=start_ms, end_ms=end_ms, text=cue.text))
    if clamped:
        debug(f"whisperx alignment clamped {clamped} cue(s) beyond {_ALIGN_MAX_ADJUST_MS}ms")
    return out


def align_cues_with_whisperx(
    *,
    media_path: Path,
    cues: list[Cue],
    device_mode: str = "auto",
    align_model: str | None = None,
) -> list[Cue]:
    _require_whisperx()

    import whisperx

    device = _resolve_device(device_mode)
    opts = AlignOptions(device=device, align_model=align_model)
    debug(f"whisperx align device resolved: mode={device_mode} device={device}")

    segments = []
    for idx, c in enumerate(cues):
        segments.append(
            {
                "id": idx,
                "text": c.text or "",
                "start": max(0.0, c.start_ms / 1000.0),
                "end": max(0.0, c.end_ms / 1000.0),
            }
        )

    def _run_align(dev: str) -> list[Cue]:
        with stage(f"align:whisperx:{media_path.name}:{dev}"):
            audio = whisperx.load_audio(str(media_path))
            model_a, metadata = whisperx.load_align_model(
                language_code="es",
                device=dev,
                model_name=opts.align_model,
            )
            aligned = whisperx.align(
                segments,
                model_a,
                metadata,
                audio,
                device=dev,
                return_char_alignments=False,
            )
            aligned_segments = aligned.get("segments") if isinstance(aligned, dict) else None
            if not isinstance(aligned_segments, list):
                raise RuntimeError("WhisperX alignment returned unexpected segments.")
            if not aligned_segments:
                debug("whisperx alignment returned empty segments; keeping original timing")
                return cues
            if len(aligned_segments) != len(cues):
                debug(
                    f"whisperx alignment segment count mismatch: got {len(aligned_segments)} "
                    f"expected {len(cues)}; attempting id-based mapping"
                )
                retimed = retime_cues_from_segments(cues, aligned_segments)
                return retimed
            return retime_cues_from_segments(cues, aligned_segments)

    try:
        retimed = _run_align(device)
    except Exception:
        if device_mode == "auto" and device == "mps":
            debug("whisperx align mps failed; retrying on cpu")
            retimed = _run_align("cpu")
        else:
            raise
    else:
        if device_mode == "auto" and device == "mps":
            debug("whisperx align used mps (no fallback)")

    debug(
        f"whisperx alignment retimed cues: total={len(retimed)} "
        f"adjusted={sum(1 for a, b in zip(cues, retimed) if a.start_ms != b.start_ms or a.end_ms != b.end_ms)}"
    )
    return retimed
