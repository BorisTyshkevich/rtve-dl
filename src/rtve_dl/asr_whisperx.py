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

    def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        debug("whisperx " + " ".join(cmd[1:]))
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    with stage(f"asr:whisperx:{media_path.name}"):
        p = _run(args)
        if p.returncode != 0:
            out = (p.stdout or "").lower()
            # WhisperX currently uses faster-whisper/ctranslate2; on many setups MPS is unsupported.
            # Auto-fallback to CPU for better out-of-the-box reliability.
            if "unsupported device mps" in out and device.lower() == "mps":
                debug("whisperx mps unsupported; retrying with cpu/float32")
                retry = list(args)
                i_dev = retry.index("--device") + 1
                i_ct = retry.index("--compute_type") + 1
                retry[i_dev] = "cpu"
                retry[i_ct] = "float32"
                p = _run(retry)
            if p.returncode != 0:
                log_path = Path(str(out_srt) + ".log")
                log_path.write_text(p.stdout or "", encoding="utf-8", errors="replace")
                out = (p.stdout or "").lower()
                if "weights only load failed" in out and "omegaconf.listconfig.listconfig" in out:
                    raise RuntimeError(
                        "whisperx failed due to incompatible torch/torchaudio versions "
                        "(common with torch>=2.6). Reinstall ASR deps with "
                        "`pip install -U -e '.[asr]'` in a Python 3.12/3.13 venv, then retry. "
                        f"Details: {log_path}"
                    )
                raise RuntimeError(f"whisperx failed (exit {p.returncode}); see {log_path}")

    produced = out_srt.parent / f"{media_path.stem}.srt"
    if not produced.exists():
        raise RuntimeError(f"whisperx finished but expected output missing: {produced}")
    if produced.resolve() != out_srt.resolve():
        produced.replace(out_srt)
