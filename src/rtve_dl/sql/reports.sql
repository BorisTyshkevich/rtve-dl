-- Usage:
-- sqlite3 tmp/<slug>/meta/telemetry.sqlite < src/rtve_dl/sql/reports.sql

.mode column
.headers on

DROP VIEW IF EXISTS report_codex_chunks;
CREATE TEMP VIEW report_codex_chunks AS
SELECT c.*
FROM codex_chunks c
JOIN runs r ON r.run_id = c.run_id
LEFT JOIN episodes e ON e.run_id = c.run_id AND e.episode_id = c.episode_id
WHERE COALESCE(r.status, '') <> 'running'
  AND COALESCE(e.status, '') <> 'running';

SELECT 'coverage_usage_parse_ok' AS section;
SELECT
  COUNT(*) AS chunks_total,
  SUM(CASE WHEN usage_parse_ok = 1 THEN 1 ELSE 0 END) AS chunks_with_usage,
  ROUND(100.0 * SUM(CASE WHEN usage_parse_ok = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS usage_coverage_pct
FROM report_codex_chunks;

SELECT 'run_summary' AS section;
SELECT
  c.run_id,
  COUNT(*) AS chunks,
  SUM(CASE WHEN c.ok = 1 THEN 1 ELSE 0 END) AS ok_chunks,
  ROUND(100.0 * SUM(CASE WHEN c.ok = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS ok_pct,
  SUM(COALESCE(c.total_tokens, 0)) AS total_tokens,
  ROUND(AVG(c.duration_ms), 1) AS avg_duration_ms
FROM report_codex_chunks c
GROUP BY c.run_id
ORDER BY MIN(c.started_at) DESC;

SELECT 'track_summary' AS section;
SELECT
  c.track_type,
  COUNT(*) AS chunks,
  SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) AS failed_chunks,
  ROUND(100.0 * SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS failed_pct,
  SUM(CASE WHEN c.fallback_used = 1 THEN 1 ELSE 0 END) AS fallback_chunks,
  SUM(COALESCE(c.total_tokens, 0)) AS total_tokens,
  ROUND(AVG(c.total_tokens), 1) AS avg_tokens_per_chunk
FROM report_codex_chunks c
GROUP BY c.track_type
ORDER BY total_tokens DESC;

SELECT 'token_efficiency' AS section;
SELECT
  c.track_type,
  ROUND(AVG(CASE WHEN c.input_items > 0 AND c.total_tokens IS NOT NULL THEN 1.0 * c.total_tokens / c.input_items END), 2) AS avg_tokens_per_item,
  MIN(CASE WHEN c.input_items > 0 AND c.total_tokens IS NOT NULL THEN 1.0 * c.total_tokens / c.input_items END) AS min_tokens_per_item,
  MAX(CASE WHEN c.input_items > 0 AND c.total_tokens IS NOT NULL THEN 1.0 * c.total_tokens / c.input_items END) AS max_tokens_per_item
FROM report_codex_chunks c
GROUP BY c.track_type
ORDER BY avg_tokens_per_item DESC;

SELECT 'storm_errors_by_model' AS section;
SELECT
  COALESCE(c.model, '<default>') AS model,
  c.track_type,
  COUNT(*) AS chunks,
  SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) AS failed,
  ROUND(100.0 * SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS failed_pct
FROM report_codex_chunks c
GROUP BY model, c.track_type
ORDER BY failed DESC, chunks DESC;

SELECT 'outlier_chunks_by_tokens' AS section;
SELECT
  c.run_id,
  c.episode_id,
  c.track_type,
  c.chunk_name,
  c.total_tokens,
  c.duration_ms,
  c.ok,
  c.fallback_used
FROM report_codex_chunks c
WHERE c.total_tokens IS NOT NULL
ORDER BY c.total_tokens DESC
LIMIT 25;

SELECT 'episode_outliers' AS section;
SELECT
  c.run_id,
  c.episode_id,
  COUNT(*) AS chunks,
  SUM(COALESCE(c.total_tokens, 0)) AS total_tokens,
  SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) AS failed_chunks,
  ROUND(AVG(c.duration_ms), 1) AS avg_duration_ms
FROM report_codex_chunks c
GROUP BY c.run_id, c.episode_id
ORDER BY total_tokens DESC
LIMIT 25;

SELECT 'time_trend_hourly' AS section;
SELECT
  strftime('%Y-%m-%d %H:00', c.started_at) AS hour_bucket,
  COUNT(*) AS chunks,
  SUM(COALESCE(c.total_tokens, 0)) AS total_tokens,
  SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) AS failed_chunks
FROM report_codex_chunks c
GROUP BY hour_bucket
ORDER BY hour_bucket DESC
LIMIT 72;
