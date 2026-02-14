from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from rtve_dl.log import error


def normalize_es_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = s.replace("\u2026", "...")
    trans = str.maketrans(
        {
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u00ab": '"',
            "\u00bb": '"',
            "\u201e": '"',
        }
    )
    s = s.translate(trans)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


@dataclass(frozen=True)
class GlobalPhraseCache:
    entries: dict[str, dict]

    def lookup(self, text: str, *, track: str) -> str | None:
        key = normalize_es_text(text)
        if not key:
            return None
        item = self.entries.get(key)
        if not isinstance(item, dict):
            return None
        if item.get("enabled", True) is False:
            return None
        value = item.get(track)
        return value if isinstance(value, str) else None

    def split_for_track(
        self,
        *,
        cues: list[tuple[str, str]],
        track: str,
    ) -> tuple[dict[str, str], list[tuple[str, str]]]:
        hits: dict[str, str] = {}
        misses: list[tuple[str, str]] = []
        for cue_id, cue_text in cues:
            cached = self.lookup(cue_text, track=track)
            if cached is None:
                misses.append((cue_id, cue_text))
            else:
                hits[cue_id] = cached
        return hits, misses


def load_global_phrase_cache(path: Path) -> GlobalPhraseCache:
    if not path.exists():
        return GlobalPhraseCache(entries={})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        error(f"global phrase cache is invalid JSON ({path}): {e}")
        return GlobalPhraseCache(entries={})

    if not isinstance(raw, dict) or int(raw.get("version", 0) or 0) != 1:
        error(f"global phrase cache version mismatch ({path}); expected version=1")
        return GlobalPhraseCache(entries={})
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return GlobalPhraseCache(entries={})
    return GlobalPhraseCache(entries=entries)
