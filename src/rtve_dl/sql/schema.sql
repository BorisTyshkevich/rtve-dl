CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  slug TEXT NOT NULL,
  selector TEXT NOT NULL,
  cli_args TEXT NOT NULL,
  app_version TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
  run_id TEXT NOT NULL,
  episode_id TEXT NOT NULL,
  base_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT,
  PRIMARY KEY (run_id, episode_id)
);

CREATE TABLE IF NOT EXISTS codex_chunks (
  run_id TEXT NOT NULL,
  episode_id TEXT NOT NULL,
  track_type TEXT NOT NULL,
  chunk_name TEXT NOT NULL,
  model TEXT,
  chunk_size INTEGER,
  input_items INTEGER,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_ms INTEGER,
  ok INTEGER NOT NULL,
  exit_code INTEGER,
  missing_ids INTEGER,
  fallback_used INTEGER NOT NULL,
  log_path TEXT,
  total_tokens INTEGER,
  usage_source TEXT NOT NULL DEFAULT 'missing',
  usage_parse_ok INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, episode_id, track_type, chunk_name, started_at)
);

CREATE INDEX IF NOT EXISTS idx_codex_chunks_run_ep_track
ON codex_chunks(run_id, episode_id, track_type);

CREATE INDEX IF NOT EXISTS idx_codex_chunks_started
ON codex_chunks(started_at);
