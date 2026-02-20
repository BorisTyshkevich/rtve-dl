# Changelog

## 0.3.0

- Subtitle timing overhaul:
  - default delay mode is `auto`, applied by shifting SRT files (mux delay = 0).
  - auto-delay is computed per episode only when ES subtitles are rebuilt.
  - EN VTT cues are shifted by the same delay; MT tracks inherit ES timing.
- Added WhisperX alignment mode:
  - optional per-cue retiming to audio (`--subtitle-align whisperx`).
  - alignment helper module + smoke test fixtures/tools.
  - alignment documentation and Apple Silicon MPS setup guide.
- Simplified delay controls and reset behavior:
  - single `--subtitle-delay auto|<ms>` flag (old modes removed).
  - ES reset clears aligned SRTs and auto-delay artifacts.
- Translation pipeline improvements:
  - no-chunk retry path uses chunked fallback with consistent chunk sizing.
  - retry TSV generation respects `use_context` and prompt expectations.
  - large cue sets auto-disable no-chunk to avoid missing ID drift.
- Performance/maintainability:
  - precomputed ID->index maps to avoid O(n^2) retries.
  - resolve calls skipped when ES SRT + MP4 already exist locally.
  - new subtitle timing documentation and tests.

## 0.2.6

- Reworked RU refs generation/output to inline annotation mode:
  - `ru_refs` prompt now expects full Spanish cue text with inline Russian glosses in brackets.
  - Added explicit good/bad examples to reduce glossary-list regressions.
  - Added cue-local refs behavior (`use_context=False`) to avoid neighboring cue bleed.
- Hardened refs parsing/composition path:
  - TSV parser now preserves all columns after `id` to tolerate model output with extra tabs.
  - Refs composer now accepts only sentence-like inline annotations and falls back to original ES cue for invalid/list-style outputs.
- Updated MKV subtitle language tags for bilingual tracks:
  - `ES+RU refs` / `ES+RU refs/ASR` -> `spa`
  - `ES+RU` / `ES+RU/ASR` -> `rus`
- Added ASR hallucination cleanup improvements:
  - new `subs/dedup.py` with within-cue and cross-cue repetition collapse.
  - integrated deduplication in ASR subtitle paths.
  - tuned MLX Whisper anti-hallucination decode settings.
- Added RU refs prompt test suite for fast manual iteration:
  - `tools/test_ru_refs_prompt.sh` (Codex/Claude backend switch, strict validator, output normalization).
  - fixtures: `tools/fixtures/ru_refs_446.tsv`, `tools/fixtures/ru_refs_small.tsv` (+ expected notes files).
  - docs: `docs/prompt_tests.md`.
- Prompt/reporting updates:
  - RU prompt templates updated for better loan-word translation behavior.
  - SQL reports extended with yesterday-focused telemetry sections.

## 0.2.5

- Added `--force-asr` mode for generating ASR-based subtitles even when RTVE provides Spanish subtitles:
  - Always runs ASR and builds parallel ASR-based translations
  - Skips regenerating RTVE-based translations (saves API costs)
  - Includes cached RTVE translations from previous runs if they exist
  - Default subtitle track becomes `ES+RU refs/ASR`
- Added ASR-specific cache directories:
  - `tmp/<slug>/codex/en_asr/`
  - `tmp/<slug>/codex/ru_asr/`
  - `tmp/<slug>/codex/ru_ref_asr/`
- Added ASR-specific SRT file naming:
  - `*.spa.asr.srt`, `*.eng.asr.srt`, `*.rus.asr.srt`
  - `*.spa_rus.asr.srt`, `*.spa_rus_full.asr.srt`
- Updated track naming to include MT suffix for translations:
  - Normal mode: `{model} MT` for machine-translated tracks
  - Force-ASR mode: `{model} MT/ASR` for ASR-based translations
- Extended reset layer handling to clear ASR-based cache files.

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
