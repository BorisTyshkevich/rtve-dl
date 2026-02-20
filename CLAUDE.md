# CLAUDE.md - Project Context for AI Assistance

This file provides context for Claude (or other AI assistants) when working on this codebase.

## Project Overview

**rtve-dl** is a Spanish video downloader for RTVE.es that produces language-learning MKV files with multiple subtitle tracks (Spanish, English, Russian). It uses AI (Claude Code or Codex) for translation and MLX Whisper ASR for speech recognition fallback.

## Quick Reference

### Entry Point
```bash
rtve_dl [series_url] <selector> [options]
# or with env vars:
RTVE_SERIES_URL="..." RTVE_SERIES_SLUG="..." rtve_dl <selector>
```
- Selector format: `T7` (season 7) or `T7S5` (season 7, episode 5)
- `series_url` can be omitted if `RTVE_SERIES_URL` env var is set
- Short flags: `-s` (slug), `-d` (debug), `-j` (jobs), `-m` (model)
- Main CLI: `src/rtve_dl/cli.py`
- Main workflow: `src/rtve_dl/workflows/download.py`

### Key Directories
```
src/rtve_dl/           # Main package
  ├── workflows/       # Orchestration
  ├── rtve/            # RTVE API integration
  ├── subs/            # Subtitle parsing (VTT, SRT)
  ├── prompts/         # AI prompt templates (packaged)
  └── sql/             # Telemetry schema & reports (packaged)

tmp/<slug>/            # Cache (per-series)
  ├── mp4/             # Downloaded videos
  ├── vtt/             # Source VTT subtitles
  ├── srt/             # Generated SRT subtitles
  ├── codex/           # Translation chunks
  │   ├── {en,ru,ru_ref}/      # RTVE-based translations
  │   ├── {en,ru,ru_ref}_asr/  # ASR-based translations
  │   └── es_clean/            # Spanish post-processing
  └── meta/            # Telemetry, catalog cache

data/<slug>/           # Output
  ├── *.mkv            # Final video files
  └── index.html       # Playback index
```

## Architecture Patterns

### Layered Cache
Layers have dependencies. Resetting a layer clears all downstream:
```
video → subs-es → subs-en
                → subs-ru
                → subs-refs
                → mkv
```

### Parallel Pipeline
- Episode-level: `-j` / `--jobs-episodes` (default: 2)
- Codex chunk-level: `--jobs-codex-chunks` (default: 4)
- Video download runs parallel with subtitle acquisition

### Translation Protocol
1. Split Spanish SRT into chunks (500 cues default) or send all cues at once (no-chunk mode)
2. Cache as JSONL (input/output pairs)
3. Send as compact TSV to backend (Claude Code default, Codex optional)
4. Parse response, write `.output.jsonl` or `.nochunk.out.jsonl`
5. Resume from existing JSONL on retry

### Translation Modes
Two execution strategies:
- **No-chunk** (Claude default): Single request with full episode context
- **Chunked** (Codex default): Parallel batches with resume/retry logic

CLI flags: `--no-chunk`, `--chunked`

### File Conventions
- `.partial.*` prefix for incomplete downloads (atomic writes)
- JSONL for durable chunk cache
- TSV for compact Codex payloads
- SQLite for telemetry

## Important Files

| File | Purpose |
|------|---------|
| `cli.py` | CLI argument parsing, entry point |
| `workflows/download.py` | Main orchestration (~1400 lines) |
| `codex_batch.py` | Translation chunking framework (Codex + Claude backends) |
| `codex_ru.py` | Spanish → Russian translation |
| `codex_ru_refs.py` | Spanish → Russian inline glosses |
| `codex_es_clean.py` | Spanish subtitle post-processing |
| `ffmpeg.py` | Video download and MKV mux |
| `telemetry.py` | SQLite logging |
| `tmp_layout.py` | Cache path helpers |
| `rtve/catalog.py` | RTVE series enumeration |
| `rtve/resolve.py` | Video URL resolution |
| `subs/vtt.py` | VTT parsing (Cue dataclass) |
| `subs/srt.py` | SRT rendering |
| `subs/dedup.py` | ASR hallucination cleanup (repetition collapse) |
| `asr_mlx.py` | MLX Whisper ASR |

## Common Tasks

### Adding a New Subtitle Track
1. Create translation function in `codex_*.py` (follow `codex_ru.py` pattern)
2. Add prompt template in `src/rtve_dl/prompts/`
3. Add cache layer in `tmp_layout.py`
4. Integrate in `workflows/download.py` parallel pipeline
5. Add to MKV mux in `ffmpeg.py:mux_mkv()`
6. Update `--reset-layer` expansion logic

### Modifying Translation Behavior
- Backend: Claude Code (default) or Codex CLI
- Chunk size: `--codex-chunk-cues` CLI option
- Model: `-m` / `--model` (auto-routes to `--claude-model` or `--codex-model`)
- Prompts: Edit files in `src/rtve_dl/prompts/`
- Fallback model: Hardcoded in `codex_batch.py`
- Force ASR mode: `--force-asr` generates ASR-based translations in parallel

### Adding CLI Options
1. Add argparse argument in `cli.py:main()` parser setup
2. Pass through to `download_selector()` call in `_cmd_download()`
3. Thread through to relevant workflow function

### Cache Reset Logic
Located in `workflows/download.py:_reset_preflight()`. Expansion rules:
- `video` → clears mp4 + all downstream
- `subs-es` → clears ES SRT/VTT + EN/RU/refs
- `catalog` → clears catalog JSON only

## Code Style

- **Type hints:** Use throughout, with dataclasses for structured data
- **Logging:** Use `log.py` helpers (info, debug, warning)
- **Paths:** Use `TmpLayout` for cache paths, avoid hardcoded strings
- **Errors:** Fail gracefully with fallbacks where possible
- **Atomicity:** Use `.partial.*` files for multi-step writes

## Dependencies

### Python (pip)
- `mlx-whisper >= 0.4` (optional, for ASR)
- `whisperx >= 3.1` (optional, alternative ASR)
- `cryptography >= 41` (optional, legacy compat)

### System (must be on PATH)
- `ffmpeg` / `ffprobe` - video operations
- `codex` - AI translation CLI (optional if using Claude Code backend)
- `curl` - download fallback

## Testing

### Manual Episode Testing
```bash
# Single episode (quick validation)
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T1S1 -s test

# With debug output
rtve_dl ... -d

# Force ASR mode (parallel ASR-based translations)
rtve_dl ... --force-asr

# Dry validation (check catalog only)
rtve_dl ... --reset-layer catalog

# Using env vars (set once, use repeatedly)
export RTVE_SERIES_URL="https://www.rtve.es/play/videos/cuentame-como-paso/"
export RTVE_SERIES_SLUG="test"
rtve_dl T1S1 -d
```

### Prompt Test Suite
For iterating on translation prompts without full episode runs:
```bash
# Test RU refs prompt with Codex
tools/test_ru_refs_prompt.sh --backend codex --model gpt-5.1-codex-mini

# Test RU refs prompt with Claude
tools/test_ru_refs_prompt.sh --backend claude --model sonnet

# Test with small fixture subset
tools/test_ru_refs_prompt.sh --backend codex --input tools/fixtures/ru_refs_small.tsv
```
See `docs/prompt_tests.md` for details.

## Telemetry Queries

Database: `tmp/<slug>/meta/telemetry.sqlite`
Report queries: `src/rtve_dl/sql/reports.sql`

```sql
-- Token usage by track
SELECT track_type, SUM(total_tokens) FROM codex_chunks GROUP BY track_type;

-- Fallback usage
SELECT model, COUNT(*) FROM codex_chunks WHERE fallback_used = 1 GROUP BY model;
```

## Known Limitations

1. **RTVE-specific:** Tightly coupled to RTVE.es API structure
2. **No DRM bypass:** Fails fast on protected content (by design)
3. **External tools:** Requires ffmpeg, codex CLI on PATH
4. **macOS focus:** MLX Whisper optimized for Apple Silicon

## Version

Current: **0.3.0** (see CHANGELOG.md for history)

## Documentation

- `README.md` - User guide
- `caches.md` - Cache internals
- `docs/architecture.md` - Repository structure
- `docs/prompt_tests.md` - Prompt iteration guide
- `CHANGELOG.md` - Version history
- `CONTRIBUTING.md` - Development guidelines
