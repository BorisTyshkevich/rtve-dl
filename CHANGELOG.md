# Changelog

## 0.2.4

- Added Spanish subtitle post-processing stage (`es_clean`) via Codex prompt templates:
  - new module `src/rtve_dl/codex_es_clean.py`
  - new prompt `src/rtve_dl/prompts/es_clean.md`
- Added dedicated ES cleanup cache directory in tmp layout:
  - `tmp/<slug>/codex/es_clean/`
  - tmp migration now routes `*.es_clean.*` artifacts into that directory.
- Added ES cleanup CLI controls:
  - `--es-postprocess` / `--no-es-postprocess`
  - `--es-postprocess-force`
  - `--es-postprocess-model`
  - `--es-postprocess-chunk-cues` (default: `100`)
- Updated ES cleanup runtime defaults:
  - default cleanup model is `gpt-5.1-codex-mini`
  - ES cleanup does not use model fallback; on failure pipeline keeps raw ES subtitles.
- Improved ASR subtitle cache behavior:
  - raw ASR output is persisted as `tmp/<slug>/srt/<base>.spa.asr_raw.srt`
  - canonical `*.spa.srt` can be rebuilt from raw cache without re-running ASR
  - `--reset-layer subs-es` clears ES/cleanup artifacts but preserves raw ASR cache.
- Documentation updates:
  - README expanded for ES post-processing controls and ASR raw cache behavior
  - `caches.md` updated with ES cleanup cache and reset semantics.

## 0.2.3

- Refactored tmp storage into structured subdirectories per slug:
  - `tmp/<slug>/mp4`
  - `tmp/<slug>/vtt`
  - `tmp/<slug>/srt`
  - `tmp/<slug>/codex/{en,ru,ru_ref}`
  - `tmp/<slug>/meta` and `tmp/<slug>/meta/legacy`
- Added `TmpLayout` and centralized cache path helpers used by downloader stages.
- Added/extended automatic tmp migration from old flat layout to new structure,
  including legacy artifacts (`*.srt.log`, `*.srt.bak.*`, unknown leftovers).
- Updated reset, catalog cache, subtitle delay cache, telemetry DB, and index metadata
  paths to use the new tmp layout.
- Moved SQL assets into package resources under `src/rtve_dl/sql/`
  (`schema.sql`, `reports.sql`).
- Added repository structure map in `docs/architecture.md`.
- Added Spanish post-ASR cleanup stage via Codex (`es_clean_light`) with fallback
  to raw ASR subtitles on cleanup failure.
- Added CLI controls for ES cleanup:
  `--es-postprocess`, `--es-postprocess-force`, `--es-postprocess-model`,
  `--es-postprocess-chunk-cues`.

## 0.2.2

- Codex prompt templates moved to package files:
  - `src/rtve_dl/prompts/ru_full.md`
  - `src/rtve_dl/prompts/ru_refs.md`
  - `src/rtve_dl/prompts/en_mt.md`
- Switched Codex payload protocol to compact TSV while keeping JSONL as durable cache.
- Changed `ru_refs` generation:
  - Codex now returns only compact Russian gloss strings per cue.
  - `Spanish|RU refs` SRT is assembled programmatically from ES cue + RU glosses.
- Added optional static global phrase cache:
  - `data/global_phrase_cache.json` with normalized exact match keys.
  - Applied before chunking for `ru_full`, `ru_refs`, and `en_mt`.
- Added chunk-level SQLite telemetry:
  - `tmp/<slug>/telemetry.sqlite` with run/episode/chunk records.
  - per-chunk token usage (`total_tokens`) parsed from Codex output when available.
  - usage quality flags: `usage_source`, `usage_parse_ok`.
- Moved telemetry SQL schema into template file:
  - `sql/schema.sql` (single source of truth for DB bootstrap)
- Added SQL report pack:
  - `sql/reports.sql` for aggregate usage/error/efficiency analytics.
- Updated Codex defaults:
  - primary model `gpt-5.1-codex-mini`
  - fallback model `gpt-5.3-codex` for failed chunks only
  - default chunk size `500` (refs internally capped to `200`)
- Hardened Codex output handling:
  - empty/unparseable chunk output now fails fast with `.log` reference.

## 0.2.1

- Changed `--reset-layer` execution model to selector-wide preflight:
  - reset now runs once for the whole selector (`T7`/`T7S9`) before processing starts
  - improved crash/restart workflow (rerun without reset continues from rebuilt cache)
- Added a new subtitle track without extra Codex cost:
  - `Spanish|Russian (Full)` dual-line subtitles (`ES + full RU`)
  - generated from existing RU translation output/cache (no additional translation pass)
- Extended cache/reset behavior for the new full bilingual track:
  - local mux precheck now includes `*.spa_rus_full.srt`
  - `subs-ru` reset removes `*.spa_rus_full.srt` and related RU artifacts
- Improved MKV subtitle disposition behavior:
  - removed duplicate disposition assignment for the default subtitle stream
  - avoids ffmpeg warning about multiple disposition options for the same stream
- Added CLI aliases:
  - `--reset` as alias for `--reset-layer`
  - `--delay` as alias for `--subtitle-delay-ms`

## 0.2.0

- Reworked downloader pipeline for high parallelism:
  - parallel episode workers for season runs
  - parallel subtitle/video stages per episode
  - parallel Codex chunk execution
  - compact per-episode debug progress states
- Added resilient media download behavior:
  - resumable MP4 downloads via `.partial.mp4`
  - safer retry handling and improved downloader logging
  - stronger local cache checks before network/resolve
- Added and stabilized subtitle generation fallbacks:
  - WhisperX fallback path for missing RTVE Spanish subtitles
  - MLX Whisper backend (Apple Silicon friendly) as primary ASR path
  - MLX model fallback (`whisper-small` -> `whisper-tiny`)
  - VAD configuration options and platform guidance updates
- Added subtitle timing controls:
  - manual subtitle offset at MKV mux stage
  - automatic subtitle delay estimation with cache and refresh control
- Added catalog and resolve optimizations:
  - RTVE catalog disk cache
  - pre-resolve short-circuit when local episode assets are complete
- Refined output/index UX:
  - richer `index.html` generation with metadata enrichment
  - JS-generated M3U links for player launch fallback
  - improved episode card/title/link behavior
- Simplified and hardened storage/caching model:
  - canonical layout: `data/<slug>/` outputs and `tmp/<slug>/` caches
  - cache reset controls via `--reset-layer`:
    `subs-es`, `subs-en`, `subs-ru`, `subs-refs`, `video`, `mkv`, `catalog`
  - reset dependency expansion and selector-scoped invalidation
- Added detailed cache internals documentation in `caches.md`
  and streamlined `README.md` around user-facing commands.

## 0.1.0

- Initial project scaffold.
- Per-series indexing: downloads ES/EN subtitles when available and extracts term frequencies.
- JSONL export/import for CEFR+gloss tasks and full Russian cue translation tasks.
- MKV mux workflow with Spanish, English (if available), three RU-gloss Spanish tracks, and full Russian translation track.
