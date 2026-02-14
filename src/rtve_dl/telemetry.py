from __future__ import annotations

import json
import importlib.resources as resources
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelemetryDB:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        schema_sql = resources.files("rtve_dl.sql").joinpath("schema.sql").read_text(encoding="utf-8")
        with self._lock:
            self._conn.executescript(schema_sql)
            self._conn.commit()

    def start_run(
        self,
        *,
        slug: str,
        selector: str,
        cli_args: dict,
        app_version: str,
    ) -> str:
        run_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO runs(run_id, slug, selector, cli_args, app_version, started_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, slug, selector, json.dumps(cli_args, ensure_ascii=False), app_version, _now_iso(), "running"),
            )
            self._conn.commit()
        return run_id

    def end_run(self, *, run_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET ended_at=?, status=? WHERE run_id=?",
                (_now_iso(), status, run_id),
            )
            self._conn.commit()

    def start_episode(self, *, run_id: str, episode_id: str, base_name: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO episodes(run_id, episode_id, base_name, started_at, ended_at, status)
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (run_id, episode_id, base_name, _now_iso(), "running"),
            )
            self._conn.commit()

    def end_episode(self, *, run_id: str, episode_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE episodes SET ended_at=?, status=? WHERE run_id=? AND episode_id=?",
                (_now_iso(), status, run_id, episode_id),
            )
            self._conn.commit()

    def record_codex_chunk(
        self,
        *,
        run_id: str,
        episode_id: str,
        track_type: str,
        chunk_name: str,
        model: str | None,
        chunk_size: int,
        input_items: int,
        started_at: str,
        ended_at: str,
        duration_ms: int,
        ok: bool,
        exit_code: int | None,
        missing_ids: int,
        fallback_used: bool,
        log_path: str | None,
        total_tokens: int | None,
        usage_source: str,
        usage_parse_ok: bool,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO codex_chunks(
                    run_id, episode_id, track_type, chunk_name, model, chunk_size, input_items,
                    started_at, ended_at, duration_ms, ok, exit_code, missing_ids, fallback_used, log_path,
                    total_tokens, usage_source, usage_parse_ok
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    episode_id,
                    track_type,
                    chunk_name,
                    model,
                    int(chunk_size),
                    int(input_items),
                    started_at,
                    ended_at,
                    int(duration_ms),
                    1 if ok else 0,
                    exit_code,
                    int(missing_ids),
                    1 if fallback_used else 0,
                    log_path,
                    total_tokens,
                    usage_source,
                    1 if usage_parse_ok else 0,
                ),
            )
            self._conn.commit()
