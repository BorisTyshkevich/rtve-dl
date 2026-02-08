from __future__ import annotations

from dataclasses import dataclass

from rtve_dl.subs.vtt import Cue


def _fmt_ms(ms: int) -> str:
    if ms < 0:
        ms = 0
    hh = ms // 3_600_000
    ms -= hh * 3_600_000
    mm = ms // 60_000
    ms -= mm * 60_000
    ss = ms // 1000
    ms -= ss * 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def cues_to_srt(cues: list[Cue]) -> str:
    out: list[str] = []
    for idx, c in enumerate(cues, start=1):
        out.append(str(idx))
        out.append(f"{_fmt_ms(c.start_ms)} --> {_fmt_ms(c.end_ms)}")
        out.append(c.text)
        out.append("")
    return "\n".join(out)

