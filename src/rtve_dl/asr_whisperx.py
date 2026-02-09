from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rtve_dl.log import debug, stage


def _require_whisperx() -> None:
    if shutil.which("whisperx") is None:
        raise RuntimeError(
            "whisperx CLI not found on PATH. Install WhisperX (e.g. `pip install whisperx`) "
            "or disable fallback with --no-asr-if-missing"
        )


def transcribe_es_to_srt_with_whisperx(
    *,
    media_path: Path,
    out_srt: Path,
    model: str,
    device: str,
    compute_type: str,
    batch_size: int,
) -> None:
    _require_whisperx()
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "whisperx",
        str(media_path),
        "--language",
        "es",
        "--task",
        "transcribe",
        "--model",
        model,
        "--device",
        device,
        "--compute_type",
        compute_type,
        "--batch_size",
        str(batch_size),
        "--output_format",
        "srt",
        "--output_dir",
        str(out_srt.parent),
    ]

    debug("whisperx " + " ".join(args[1:]))
    with stage(f"asr:whisperx:{media_path.name}"):
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if p.returncode != 0:
            log_path = Path(str(out_srt) + ".log")
            log_path.write_text(p.stdout or "", encoding="utf-8", errors="replace")
            raise RuntimeError(f"whisperx failed (exit {p.returncode}); see {log_path}")

    produced = out_srt.parent / f"{media_path.stem}.srt"
    if not produced.exists():
        raise RuntimeError(f"whisperx finished but expected output missing: {produced}")
    if produced.resolve() != out_srt.resolve():
        produced.replace(out_srt)
