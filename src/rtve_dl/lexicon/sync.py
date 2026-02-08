from __future__ import annotations

import sqlite3


_VALID_CEFR = {"A1", "A2", "B1", "B2", "C1", "C2", "UNK"}


def _parse_tsv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        header = None
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            if header is None:
                header = [c.strip() for c in cols]
                continue
            if len(cols) < 4:
                continue
            obj = dict(zip(header, cols, strict=False))
            rows.append(obj)
    return rows


def sync_lexicon_tsv_into_gloss(con: sqlite3.Connection, *, kind: str, tsv_path: str) -> int:
    """
    Load `lexicon_words.tsv` / `lexicon_phrases.tsv` into `gloss`.

    TSV format:
      term<TAB>cefr<TAB>skip<TAB>ru
    """
    if kind not in ("word", "phrase"):
        raise ValueError("kind must be word|phrase")
    rows = _parse_tsv(tsv_path)
    n = 0
    for r in rows:
        term = (r.get("term") or "").strip().lower()
        if not term:
            continue
        cefr = (r.get("cefr") or "UNK").strip().upper()
        if cefr not in _VALID_CEFR:
            cefr = "UNK"
        skip_raw = (r.get("skip") or "").strip().lower()
        skip = 1 if skip_raw in ("1", "true", "yes", "y") else 0
        ru = (r.get("ru") or "").strip()
        if skip:
            ru = ""
        con.execute(
            "INSERT OR REPLACE INTO gloss(term, kind, cefr, skip, ru) VALUES(?,?,?,?,?)",
            (term, kind, cefr, skip, ru),
        )
        n += 1
    return n

