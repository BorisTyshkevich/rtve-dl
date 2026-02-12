from __future__ import annotations

from pathlib import Path

from rtve_dl.log import debug, stage


def _fmt_srt_ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    hh = ms // 3_600_000
    ms -= hh * 3_600_000
    mm = ms // 60_000
    ms -= mm * 60_000
    ss = ms // 1000
    ms -= ss * 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def transcribe_es_to_srt_with_mlx_whisper(
    *,
    media_path: Path,
    out_srt: Path,
    model_repo: str,
) -> None:
    try:
        import mlx_whisper  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "mlx-whisper is not installed in this environment. "
            "Install with `pip install -e '.[asr]'`."
        ) from e

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    model_candidates = [model_repo]
    # Keep resilient fallbacks for common model-id mismatches.
    if model_repo == "mlx-community/whisper-small":
        model_candidates.extend(
            [
                "mlx-community/whisper-small-mlx",
                "mlx-community/whisper-tiny-mlx",
                "mlx-community/whisper-tiny",
            ]
        )
    elif model_repo == "mlx-community/whisper-small-mlx":
        model_candidates.extend(["mlx-community/whisper-tiny-mlx", "mlx-community/whisper-tiny"])

    result = None
    last_err: Exception | None = None
    for candidate in model_candidates:
        debug(f"mlx_whisper transcribe media={media_path} model={candidate}")
        try:
            with stage(f"asr:mlx:{media_path.name}"):
                result = mlx_whisper.transcribe(
                    str(media_path),
                    path_or_hf_repo=candidate,
                    task="transcribe",
                    language="es",
                    word_timestamps=False,
                    verbose=False,
                )
            break
        except Exception as e:
            last_err = e if isinstance(e, Exception) else RuntimeError(str(e))
            if candidate != model_candidates[-1]:
                debug(f"mlx_whisper model failed ({candidate}), trying fallback")
            else:
                raise

    segments = result.get("segments", []) if isinstance(result, dict) else []
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("mlx-whisper returned no segments") from last_err

    with out_srt.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            if not isinstance(seg, dict):
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            f.write(f"{i}\n")
            f.write(f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n")
            f.write(text + "\n\n")
