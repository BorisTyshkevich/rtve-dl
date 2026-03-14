from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rtve_dl.subs.vtt import Cue


@dataclass(frozen=True)
class ShiftSummary:
    count: int
    median_ms: int
    min_ms: int
    max_ms: int
    unique_count: int
    unique_sample_ms: tuple[int, ...]


@dataclass(frozen=True)
class RustInspectorEstimate:
    delay_ms: int
    confidence: float


def summarize_shift_deltas(original: list[Cue], shifted: list[Cue]) -> ShiftSummary:
    if len(original) != len(shifted):
        raise ValueError(f"cue count mismatch: original={len(original)} shifted={len(shifted)}")
    if not original:
        raise ValueError("no cues to compare")
    deltas = sorted(dst.start_ms - src.start_ms for src, dst in zip(original, shifted))
    unique = tuple(sorted(set(deltas)))
    return ShiftSummary(
        count=len(deltas),
        median_ms=deltas[len(deltas) // 2],
        min_ms=deltas[0],
        max_ms=deltas[-1],
        unique_count=len(unique),
        unique_sample_ms=unique[:10],
    )


def resolve_ilass_binary(explicit: str | None = None) -> str:
    candidates = [explicit] if explicit else []
    candidates.extend(["ilass", str(Path.home() / ".cargo" / "bin" / "ilass")])
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("ilass binary not found; install with `cargo install ilass-cli`")


def run_ilass(*, ilass_bin: str, reference_path: Path, input_srt: Path, output_srt: Path) -> None:
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ilass_bin, str(reference_path), str(input_srt), str(output_srt)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ilass failed:\n{p.stdout}")


_RUST_FINAL_RE = re.compile(r"^final:\s+delay_ms=(?P<delay>-?\d+)\s+confidence=(?P<confidence>\d+(?:\.\d+)?)\s*$")


def parse_rust_inspector_output(stdout: str) -> RustInspectorEstimate | None:
    for line in stdout.splitlines():
        match = _RUST_FINAL_RE.match(line.strip())
        if match:
            return RustInspectorEstimate(
                delay_ms=int(match.group("delay")),
                confidence=float(match.group("confidence")),
            )
    return None
