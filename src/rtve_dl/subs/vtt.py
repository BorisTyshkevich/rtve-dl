from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str  # plain text (no VTT markup)


_TS_RE = re.compile(
    r"^(?P<s>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})\s+-->\s+(?P<e>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})"
)


def _parse_ts(ts: str) -> int:
    # WebVTT timestamps: HH:MM:SS.mmm or MM:SS.mmm
    parts = ts.split(":")
    if len(parts) == 3:
        hh = int(parts[0])
        mm = int(parts[1])
        ss, ms = parts[2].split(".")
        return ((hh * 60 + mm) * 60 + int(ss)) * 1000 + int(ms)
    if len(parts) == 2:
        mm = int(parts[0])
        ss, ms = parts[1].split(".")
        return (mm * 60 + int(ss)) * 1000 + int(ms)
    raise ValueError(f"bad timestamp: {ts}")


_TAG_RE = re.compile(r"</?[^>]+>")


def vtt_to_plain_text(s: str) -> str:
    # Strip simple markup like <c.vtt_cyan>...</c> and other tags.
    s = _TAG_RE.sub("", s)
    s = s.replace("&nbsp;", " ")
    return s.strip()


def parse_vtt(vtt_text: str) -> list[Cue]:
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    i = 0
    # Skip header
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines) and lines[i].strip().startswith("WEBVTT"):
        i += 1
    # Parse blocks separated by blank lines
    while i < len(lines):
        # Skip blank lines
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        if i >= len(lines):
            break

        # Optional cue id
        if i + 1 < len(lines) and _TS_RE.match(lines[i + 1].strip()):
            i += 1

        m = _TS_RE.match(lines[i].strip())
        if not m:
            # NOTE/STYLE/REGION or garbage; skip until blank
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
        text = vtt_to_plain_text("\n".join(text_lines))
        cues.append(Cue(start_ms=start_ms, end_ms=end_ms, text=text))

    return cues

