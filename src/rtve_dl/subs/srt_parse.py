from __future__ import annotations

import re

from rtve_dl.subs.vtt import Cue

_TS_RE = re.compile(r"^(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<e>\d{2}:\d{2}:\d{2},\d{3})")


def _parse_ts(ts: str) -> int:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(ms)


def parse_srt(srt_text: str) -> list[Cue]:
    lines = srt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        if i >= len(lines):
            break

        # Optional numeric cue id.
        if i + 1 < len(lines) and lines[i].strip().isdigit() and _TS_RE.match(lines[i + 1].strip()):
            i += 1

        m = _TS_RE.match(lines[i].strip())
        if not m:
            while i < len(lines) and lines[i].strip() != "":
                i += 1
            continue

        start_ms = _parse_ts(m.group("s"))
        end_ms = _parse_ts(m.group("e"))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1
        cues.append(Cue(start_ms=start_ms, end_ms=end_ms, text="\n".join(text_lines).strip()))
    return cues
