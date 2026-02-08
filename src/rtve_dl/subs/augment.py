from __future__ import annotations

import re
import sqlite3

from rtve_dl.subs.vtt import Cue


def _cefr_ge(a: str, b: str) -> bool:
    order = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "UNK": 0}
    return order.get(a, 0) >= order.get(b, 0)


def _threshold_for_track(track: str) -> str:
    # Track names defined by the project spec.
    # A1+ includes A2 and above; A2+ includes B1 and above; B1+ includes B2 and above.
    if track == "A1+":
        return "A2"
    if track == "A2+":
        return "B1"
    if track == "B1+":
        return "B2"
    raise ValueError(track)


def load_gloss_map(con: sqlite3.Connection, *, kind: str, min_cefr: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT term, cefr, skip, ru FROM gloss WHERE kind=? AND skip=0 AND ru<>''", (kind,)
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        if _cefr_ge(r["cefr"], min_cefr):
            out[r["term"]] = r["ru"]
    return out


def _apply_phrases(text: str, phrases: list[tuple[str, str]]) -> str:
    # Longest match first; case-insensitive, but we insert preserving original span.
    for es, ru in phrases:
        pat = re.compile(rf"(?i)\b{re.escape(es)}\b")
        def repl(m):
            span = m.group(0)
            # Avoid duplicating if already glossed right after span.
            after = text[m.end() : m.end() + len(ru) + 2]
            if after.startswith("("):
                return span
            return f"{span}({ru})"
        text = pat.sub(repl, text)
    return text


def _apply_words(text: str, words: list[tuple[str, str]]) -> str:
    for es, ru in words:
        pat = re.compile(rf"(?i)\b{re.escape(es)}\b")
        def repl(m):
            span = m.group(0)
            after = text[m.end() : m.end() + len(ru) + 2]
            if after.startswith("("):
                return span
            return f"{span}({ru})"
        text = pat.sub(repl, text)
    return text


def augment_cues_with_glosses(con: sqlite3.Connection, cues: list[Cue], track: str) -> list[Cue]:
    min_cefr = _threshold_for_track(track)

    phrases_map = load_gloss_map(con, kind="phrase", min_cefr=min_cefr)
    words_map = load_gloss_map(con, kind="word", min_cefr=min_cefr)

    phrases = sorted(phrases_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    words = sorted(words_map.items(), key=lambda kv: len(kv[0]), reverse=True)

    out: list[Cue] = []
    for c in cues:
        t = c.text
        t = _apply_phrases(t, phrases)
        t = _apply_words(t, words)
        out.append(Cue(start_ms=c.start_ms, end_ms=c.end_ms, text=t))
    return out

