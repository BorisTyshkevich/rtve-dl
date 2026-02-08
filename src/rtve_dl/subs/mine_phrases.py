from __future__ import annotations

import json
import sqlite3

from rtve_dl.subs.terms import extract_words


def _is_all_stop(tokens: list[str], stop: set[str]) -> bool:
    return all((t in stop) for t in tokens)


def _stop_ratio(tokens: list[str], stop: set[str]) -> float:
    if not tokens:
        return 1.0
    s = sum(1 for t in tokens if t in stop)
    return s / len(tokens)


def mine_phrases_into_terms(
    con: sqlite3.Connection,
    *,
    stopwords: set[str],
    asset_ids: set[str] | None = None,
    min_n: int = 2,
    max_n: int = 5,
    min_count: int = 5,
    limit: int = 5000,
) -> int:
    """
    Compute frequent n-grams from already-stored Spanish cues and persist them into `terms(kind='phrase')`.

    This is intentionally separate from `index` because naive phrase extraction (all n-grams per cue) explodes
    the number of DB inserts and makes indexing the whole series impractically slow.
    """
    if min_n < 2 or max_n < min_n:
        raise ValueError("bad n-gram range")
    if min_count < 2:
        raise ValueError("min_count must be >= 2")
    if limit <= 0:
        raise ValueError("limit must be positive")

    # Load cues.
    if asset_ids:
        qs = ",".join(["?"] * len(asset_ids))
        rows = con.execute(
            f"SELECT asset_id, text FROM cues WHERE lang='es' AND asset_id IN ({qs})",
            tuple(sorted(asset_ids)),
        ).fetchall()
    else:
        rows = con.execute("SELECT asset_id, text FROM cues WHERE lang='es'").fetchall()

    # Count n-grams in memory.
    counts: dict[str, int] = {}
    contexts: dict[str, str] = {}
    for r in rows:
        text = r["text"]
        toks = [t for t in extract_words(text) if t]
        if len(toks) < min_n:
            continue

        # Keep stopwords in tokens (idioms often include them), but skip obviously useless phrases.
        for n in range(min_n, max_n + 1):
            if len(toks) < n:
                continue
            for i in range(0, len(toks) - n + 1):
                gram_toks = toks[i : i + n]
                if _is_all_stop(gram_toks, stopwords):
                    continue
                if _stop_ratio(gram_toks, stopwords) > 0.60:
                    continue
                phrase = " ".join(gram_toks)
                counts[phrase] = counts.get(phrase, 0) + 1
                # Keep one example context (first seen) to drive translation.
                if phrase not in contexts:
                    contexts[phrase] = json.dumps([text], ensure_ascii=False)

    # Select top phrases.
    items = [(p, c) for (p, c) in counts.items() if c >= min_count]
    items.sort(key=lambda kv: kv[1], reverse=True)
    items = items[:limit]

    # Persist.
    for phrase, c in items:
        con.execute(
            """
            INSERT INTO terms(term, kind, count, contexts_json)
            VALUES(?, 'phrase', ?, ?)
            ON CONFLICT(term, kind) DO UPDATE SET
              count = excluded.count
            """,
            (phrase, c, contexts.get(phrase, "[]")),
        )
    return len(items)

