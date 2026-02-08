from __future__ import annotations

import json
from pathlib import Path

from rtve_dl.lexicon.store import SeriesStore


def _jsonl_write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def export_gloss_tasks(store: SeriesStore, threshold: str) -> str:
    con = store.connect()
    try:
        rows = con.execute(
            """
            SELECT t.term, t.kind, t.contexts_json
            FROM terms t
            LEFT JOIN gloss g ON g.term = t.term AND g.kind = t.kind
            WHERE g.term IS NULL
            ORDER BY t.count DESC
            LIMIT 5000
            """
        ).fetchall()
    finally:
        con.close()

    tasks: list[dict] = []
    for r in rows:
        contexts = json.loads(r["contexts_json"])
        tasks.append(
            {
                "id": f'{r["kind"]}:{r["term"]}',
                "term": r["term"],
                "kind": r["kind"],
                "threshold": threshold,
                "contexts": contexts[:3],
            }
        )

    out = store.root_dir / f"tasks_gloss_{threshold}.jsonl"
    _jsonl_write(out, tasks)
    return str(out)


def import_gloss_results(store: SeriesStore, results_jsonl: str) -> None:
    p = Path(results_jsonl)
    con = store.connect()
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                term = obj["term"]
                kind = "phrase" if obj.get("kind") == "phrase" else "word"
                cefr = obj["cefr"]
                skip = 1 if obj["skip"] else 0
                ru = obj.get("ru") or ""
                con.execute(
                    "INSERT OR REPLACE INTO gloss(term, kind, cefr, skip, ru) VALUES(?,?,?,?,?)",
                    (term, kind, cefr, skip, ru),
                )
        con.commit()
    finally:
        con.close()


def export_ru_tasks(store: SeriesStore) -> str:
    con = store.connect()
    try:
        rows = con.execute(
            """
            SELECT c.cue_id, c.text,
                   (SELECT text FROM cues c2 WHERE c2.asset_id=c.asset_id AND c2.lang=c.lang AND c2.idx=c.idx-1) AS before_text,
                   (SELECT text FROM cues c3 WHERE c3.asset_id=c.asset_id AND c3.lang=c.lang AND c3.idx=c.idx+1) AS after_text
            FROM cues c
            LEFT JOIN ru_cues r ON r.cue_id = c.cue_id
            WHERE c.lang='es' AND r.cue_id IS NULL
            ORDER BY c.asset_id, c.idx
            """
        ).fetchall()
    finally:
        con.close()

    tasks: list[dict] = []
    for r in rows:
        tasks.append(
            {
                "id": r["cue_id"],
                "text": r["text"],
                "context_before": r["before_text"] or "",
                "context_after": r["after_text"] or "",
            }
        )

    out = store.root_dir / "tasks_ru_full.jsonl"
    _jsonl_write(out, tasks)
    return str(out)


def import_ru_results(store: SeriesStore, results_jsonl: str) -> None:
    p = Path(results_jsonl)
    con = store.connect()
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                cue_id = obj["id"]
                ru = obj.get("ru") or ""
                con.execute("INSERT OR REPLACE INTO ru_cues(cue_id, ru) VALUES(?, ?)", (cue_id, ru))
        con.commit()
    finally:
        con.close()
