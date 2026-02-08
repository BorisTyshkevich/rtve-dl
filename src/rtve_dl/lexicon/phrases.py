from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def clear_mined_phrases(con: sqlite3.Connection) -> int:
    """
    Remove mined phrase candidates from the `terms` table.

    This does NOT touch `gloss` (your curated translations) and is safe to run
    before re-mining with new stopwords/settings.
    """
    cur = con.execute("DELETE FROM terms WHERE kind='phrase'")
    # sqlite3 in Python returns rowcount for DELETE.
    return int(cur.rowcount or 0)


def export_phrase_candidates_tsv(
    con: sqlite3.Connection,
    *,
    out_path: Path,
    limit: int = 2000,
    min_count: int = 2,
) -> Path:
    """
    Export mined phrases to a TSV for manual review/curation.

    Columns:
      term, count, example
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        """
        SELECT term, count, contexts_json
        FROM terms
        WHERE kind='phrase' AND count >= ?
        ORDER BY count DESC, term ASC
        LIMIT ?
        """,
        (min_count, limit),
    ).fetchall()

    with out_path.open("w", encoding="utf-8") as f:
        f.write("term\tcount\texample\n")
        for r in rows:
            term = (r["term"] or "").strip()
            cnt = int(r["count"] or 0)
            ex = ""
            try:
                ctx = json.loads(r["contexts_json"] or "[]")
                if isinstance(ctx, list) and ctx:
                    ex = str(ctx[0])
            except Exception:
                ex = ""
            # Keep TSV one-line cells.
            ex = ex.replace("\t", " ").replace("\n", " ").strip()
            f.write(f"{term}\t{cnt}\t{ex}\n")

    return out_path

