from __future__ import annotations

import re


_WORD_RE = re.compile(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]+", re.UNICODE)


def extract_words(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def extract_phrases(tokens: list[str], min_n: int = 2, max_n: int = 5) -> list[str]:
    out: list[str] = []
    t = [x for x in tokens if x]
    for n in range(min_n, max_n + 1):
        if len(t) < n:
            continue
        for i in range(0, len(t) - n + 1):
            out.append(" ".join(t[i : i + n]))
    return out

