# Architecture

## Overview

`rtve-dl` is a pipeline-oriented downloader for RTVE episodes with subtitle enrichment and MKV packaging.

High-level flow:

```text
CLI
  -> Catalog (RTVE series listing, cached)
  -> Episode selection (season or single episode)
  -> Per-episode processing
       -> Resolve episode metadata + direct media/subtitle URLs
       -> Download/reuse MP4
       -> Build ES subtitles (RTVE VTT or ASR fallback)
       -> Optional ES cleanup (Codex, post-ASR by default)
       -> Build EN subtitles (RTVE EN or ES->EN MT fallback)
       -> Build RU subtitles (full RU + RU refs)
       -> Build bilingual track (ES + full RU) from existing RU map
       -> Mux MKV with subtitle delay applied at mux stage
  -> Generate/update slug index.html
```

Execution is parallel at two levels:
- episode-level workers (`--jobs-episodes`)
- chunk-level Codex workers (`--jobs-codex-chunks`)

## Module Map

```text
src/rtve_dl/
├── cli.py                    # CLI entrypoint and argument mapping to workflow
├── workflows/download.py     # Main orchestration pipeline
├── rtve/                     # RTVE catalog/resolve/download URL logic
├── subs/                     # VTT/SRT parse/render + subtitle timing helpers
├── ffmpeg.py                 # MP4 download and MKV mux operations
├── asr_mlx.py                # MLX Whisper backend (Apple Silicon friendly)
├── asr_whisperx.py           # WhisperX backend
├── codex_batch.py            # Shared Codex chunk engine (TSV payload + JSONL cache)
├── codex_ru.py               # ES -> RU full translation
├── codex_ru_refs.py          # ES -> RU gloss references (B2/C1/C2)
├── codex_en.py               # ES -> EN fallback translation
├── codex_es_clean.py         # Spanish subtitle cleanup pass
├── global_phrase_cache.py    # Optional static phrase cache for Codex calls
├── telemetry.py              # SQLite telemetry (runs/episodes/chunks)
├── tmp_layout.py             # tmp layout, path helpers, legacy migration
├── index_html.py             # Per-slug catalog/index generation
├── prompts/                  # Prompt templates packaged with code
│   ├── ru_full.md
│   ├── ru_refs.md
│   ├── en_mt.md
│   └── es_clean.md
└── sql/                      # Runtime SQL resources
    ├── schema.sql
    ├── reports.sql
    └── migrations/           # Reserved for future schema upgrades
```

## Cache and Data Boundaries

Runtime state lives outside source tree:
- `tmp/<slug>/...` for cache/work artifacts
- `data/<slug>/...` for final outputs (`*.mkv`, `index.html`, downloadable assets)

Key tmp buckets:
- `tmp/<slug>/mp4/` video cache
- `tmp/<slug>/vtt/` raw subtitle downloads
- `tmp/<slug>/srt/` built subtitle tracks
- `tmp/<slug>/codex/{ru,ru_ref,en,es_clean}/` chunk input/output/log caches
- `tmp/<slug>/meta/` catalog cache, delay cache, telemetry DB

Layer reset is dependency-aware via `--reset-layer`/`--reset`.

## Codex Processing Model

Codex operations are chunked and resumable:
- Input cues are split into deterministic chunks.
- Per chunk, pipeline stores TSV/JSONL artifacts and logs in `tmp/<slug>/codex/...`.
- Existing non-empty output chunks are reused (`resume=True`).
- Missing/failed chunks are retried with smaller chunk sizes.

Current prompt modes:
- `translate_ru` -> full Russian subtitle track
- `ru_refs_b2plus` -> Russian glossary references for difficult ES terms
- `translate_en` -> fallback English track
- `es_clean_light` -> light editorial cleanup for Spanish subtitles

## Subtitle Tracks in Output MKV

Typical muxed tracks:
- `Spanish`
- `English` or `English (MT)`
- `Russian`
- `Spanish|RU refs` (learning-focused references)
- `Spanish|Russian (Full)` (full bilingual line pair)

Default subtitle track is set to `Spanish|RU refs`.

## Why `src/rtve_dl` and Packaged SQL

The project uses Python `src` layout to prevent accidental repo-root imports and keep install/runtime behavior predictable.

`schema.sql` and `reports.sql` are packaged in `src/rtve_dl/sql` and loaded via `importlib.resources`, so DB schema/report logic stays version-aligned with code.
