# Rust TODO

This file is the implementation tracker for the Rust pipeline.

Rules:

- every existing feature should be covered by tests
- every new feature must ship with tests in the same change
- do not mark parity complete without behavior-level tests

Status markers:

- `[DONE]` implemented and at least minimally verified
- `[PARTIAL]` implemented but not at Python parity or not covered well enough by tests
- `[TODO]` not implemented yet
- `[BLOCKED]` waiting on a product or technical decision

## Current Baseline

### Existing feature coverage policy

- `[TODO]` Add explicit tests for every already-existing Rust feature.
  What this means:
  - parsing logic gets unit tests
  - workflow decisions get unit tests
  - output generation gets golden or command-construction tests
  - end-to-end behavior gets fixture-driven integration tests

Recommended rule for every new feature:

1. Add parser/decision tests.
2. Add fixture-based behavior tests.
3. Add workflow/integration coverage.
4. Only then mark the feature complete here.

## Existing Rust Features

### Core pipeline

- `[DONE]` RTVE catalog and asset resolution.
  Rust files:
  - [rtve.rs](/Users/bvt/work/Cuentame/downloader/rust/src/rtve.rs)

- `[DONE]` MP4 download.
  Rust files:
  - [ffmpeg.rs](/Users/bvt/work/Cuentame/downloader/rust/src/ffmpeg.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)

- `[DONE]` MKV mux.
  Rust files:
  - [ffmpeg.rs](/Users/bvt/work/Cuentame/downloader/rust/src/ffmpeg.rs)

- `[PARTIAL]` `index.html` generation exists, but parity with Python is not there yet.
  Rust files:
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs#L264)
  Python reference:
  - [index_html.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/index_html.py)

### Subtitle parsing and timing

- `[DONE]` VTT and SRT parsing/rendering.
  Rust files:
  - [subs.rs](/Users/bvt/work/Cuentame/downloader/rust/src/subs.rs)

- `[PARTIAL]` Subtitle auto-delay.
  What it is:
  - Estimate one global subtitle shift from subtitle timing and audio.
  - Use energy first, then ASR fallback when energy confidence is low.
  Rust files:
  - [delay.rs](/Users/bvt/work/Cuentame/downloader/rust/src/delay.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs#L395)
  Python reference:
  - [delay_auto.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/subs/delay_auto.py)
  Missing test work:
  - `[TODO]` energy scoring tests
  - `[TODO]` cue shifting tests
  - `[DONE]` ASR cluster selection unit test
  - `[TODO]` manual delay regression tests
  - `[TODO]` VTT-based prod parity tests

- `[TODO]` `--subtitle-align whisperx`
  What it is:
  - Per-cue retiming against audio using WhisperX.
  - Different from global delay; each cue can move independently.
  Why it matters:
  - This is the biggest subtitle-timing gap versus Python.
  Constraint:
  - do not add a new Python helper/runtime dependency for Rust alignment
  - if WhisperX remains the alignment backend, Rust should call it directly in a stable way or clearly isolate any temporary bridge and remove it
  Alternative under evaluation:
  - `ilass-cli` looks useful as a non-Python subtitle synchronizer, but current local comparison on `S08E20` does not match the current WhisperX delay/alignment path closely enough to treat it as a drop-in replacement yet
  - keep `ilass-cli` as a candidate for a separate sync backend unless broader comparisons prove parity
  Rust files to extend:
  - [cli.rs](/Users/bvt/work/Cuentame/downloader/rust/src/cli.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Python reference:
  - [align_whisperx.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/subs/align_whisperx.py)
  - [download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Required tests:
  - `[TODO]` CLI parser tests for alignment flags
  - `[TODO]` alignment path decision tests
  - `[TODO]` fixture-based alignment integration test
  - `[TODO]` mux/output selection test for aligned ES
  - `[TODO]` regression test for interaction with subtitle auto-delay

### ASR

- `[PARTIAL]` Missing-ES ASR fallback.
  What it is:
  - If RTVE has no Spanish subtitles, generate `spa.srt` from ASR.
  Rust files:
  - [asr.rs](/Users/bvt/work/Cuentame/downloader/rust/src/asr.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs#L375)
  Python reference:
  - [workflows/download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Missing test work:
  - `[TODO]` fixture-driven missing-ES workflow test
  - `[TODO]` transcript reuse test
  - `[TODO]` dedup behavior test in workflow context

- `[DONE]` ASR abstraction.
  Rust files:
  - [asr.rs](/Users/bvt/work/Cuentame/downloader/rust/src/asr.rs)

- `[PARTIAL]` Two ASR backends exist: `whisperx` and explicit `whisper-rs`.
  What it is:
  - `auto` currently resolves to `whisperx`
  - `whisper-rs` is available explicitly and built with `metal`
  Rust files:
  - [asr.rs](/Users/bvt/work/Cuentame/downloader/rust/src/asr.rs)
  - [Cargo.toml](/Users/bvt/work/Cuentame/downloader/rust/Cargo.toml)
  Remaining work:
  - `[TODO]` backend-quality comparison tests
  - `[TODO]` decide long-term default backend based on quality, not infrastructure

- `[TODO]` Full ASR feature parity
  What it is:
  - The complete ASR story, not only missing-ES fallback.
  Includes:
  - `--force-asr`
  - ASR-derived track variants
  - documented backend roles
  Why it matters:
  - Subtitle availability and timing quality depend on this.
  Rust files to extend:
  - [cli.rs](/Users/bvt/work/Cuentame/downloader/rust/src/cli.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Python reference:
  - [cli.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/cli.py)
  - [download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Required tests:
  - `[TODO]` backend contract tests for `whisperx`
  - `[PARTIAL]` backend contract tests for `whisper-rs`
  - `[TODO]` `--force-asr` integration tests
  - `[TODO]` ASR-specific track variant tests
  - `[TODO]` transcript caching tests

### ES post-processing

- `[TODO]` ES post-processing
  What it is:
  - Cleanup pass for ASR-generated Spanish subtitles.
  - Improve punctuation, casing, readability, and small recognition artifacts.
  Why it matters:
  - Raw ASR output is often usable but not final-quality.
  Rust files to extend:
  - [cli.rs](/Users/bvt/work/Cuentame/downloader/rust/src/cli.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Python reference:
  - [codex_es_clean.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/codex_es_clean.py)
  - [workflows/download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Required tests:
  - `[TODO]` CLI flag tests
  - `[TODO]` invocation/cache tests
  - `[TODO]` text-cleanup fixture tests
  - `[TODO]` timing-preservation regression tests

## New Feature Order

### 1. `--subtitle-align whisperx`

- `[TODO]` Implement.
- `[TODO]` Add tests before marking done.

### 2. Global phrase cache

- `[TODO]` Implement.
  What it is:
  - Shared phrase/term cache used to keep translations consistent across runs.
  Python reference:
  - [global_phrase_cache.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/global_phrase_cache.py)
  - [workflows/download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Rust files likely affected:
  - [translate.rs](/Users/bvt/work/Cuentame/downloader/rust/src/translate.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Required tests:
  - `[TODO]` cache read/write tests
  - `[TODO]` deterministic merge tests
  - `[TODO]` translation consistency tests
  - `[TODO]` malformed-cache regression tests

### 3. `index.html`

- `[PARTIAL]` Simple implementation exists; parity version still needed.
  What it is:
  - Per-series playback and browsing page in `data/<slug>/index.html`.
  Rust files:
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs#L264)
  Python reference:
  - [index_html.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/index_html.py)
  Required tests:
  - `[TODO]` golden-file HTML tests
  - `[TODO]` ordering tests
  - `[TODO]` missing-file behavior tests
  - `[TODO]` regeneration integration test

### 4. ASR

- `[TODO]` Finish full ASR feature set as described above.

### 5. ES post-processing

- `[TODO]` Implement as described above.

### 6. Retry narrowing and fallback model retry

- `[TODO]` Implement.
  What it is:
  - Retry translation failures with smaller chunk sizes and optionally a fallback model.
  Why it matters:
  - This is one of Python’s main reliability advantages.
  Rust files likely affected:
  - [translate.rs](/Users/bvt/work/Cuentame/downloader/rust/src/translate.rs)
  Python reference:
  - [codex_batch.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/codex_batch.py)
  Required tests:
  - `[TODO]` retry planner tests
  - `[TODO]` backend failure simulation tests
  - `[TODO]` fallback model selection tests
  - `[TODO]` partial-success regression tests

### 7. Salvage/resume logic

- `[TODO]` Implement.
  What it is:
  - Recover usable translation output from partial responses and resume interrupted work from cached artifacts.
  Why it matters:
  - Long translation jobs should not restart from zero after minor failures.
  Rust files likely affected:
  - [translate.rs](/Users/bvt/work/Cuentame/downloader/rust/src/translate.rs)
  Python reference:
  - [codex_batch.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/codex_batch.py)
  Required tests:
  - `[TODO]` partial TSV salvage tests
  - `[TODO]` interrupted-run resume tests
  - `[TODO]` duplicate/missing-row tests
  - `[TODO]` deterministic final-assembly tests

### 8. Telemetry

- `[TODO]` Implement.
  What it is:
  - SQLite-backed run and chunk telemetry for timing, retries, tokens, failures, and model usage.
  Why it matters:
  - Needed for debugging, cost tracking, and parity with Python.
  Python reference:
  - [telemetry.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/telemetry.py)
  - [sql/reports.sql](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/sql/reports.sql)
  Rust files likely affected:
  - new telemetry module
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  - [translate.rs](/Users/bvt/work/Cuentame/downloader/rust/src/translate.rs)
  Required tests:
  - `[TODO]` schema creation tests
  - `[TODO]` run/episode write tests
  - `[TODO]` chunk telemetry tests
  - `[TODO]` failure-path telemetry tests

### 9. Cache/reset parity and media consistency checks

- `[PARTIAL]` Reset expansion exists, but parity is incomplete.
  What it is:
  - Match Python behavior for cache layout, reset propagation, empty-file cleanup, and MP4/subtitle consistency validation.
  Rust files:
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Python reference:
  - [tmp_layout.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/tmp_layout.py)
  - [workflows/download.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/workflows/download.py)
  Required tests:
  - `[TODO]` reset expansion tests
  - `[TODO]` file matching tests
  - `[TODO]` empty-file cleanup tests
  - `[TODO]` duration consistency tests
  - `[TODO]` redownload/rebuild decision tests

## Translation and Output Parity

### Translation pipeline

- `[PARTIAL]` Chunked translation exists.
  Rust files:
  - [translate.rs](/Users/bvt/work/Cuentame/downloader/rust/src/translate.rs)
  Remaining gaps:
  - `[TODO]` `ru_ref` stability
  - `[TODO]` no-chunk mode
  - `[TODO]` retry narrowing
  - `[TODO]` fallback model retry
  - `[TODO]` salvage/resume logic
  Required tests:
  - `[TODO]` malformed TSV tests
  - `[TODO]` missing row tests
  - `[TODO]` leading commentary tests
  - `[TODO]` multiline content tests

### Track generation

- `[PARTIAL]` `es`, `en`, `ru`, `refs`, `ru-dual` tracks exist.
  Rust files:
  - [tracks.rs](/Users/bvt/work/Cuentame/downloader/rust/src/tracks.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)
  Remaining gaps:
  - `[TODO]` ASR-specific track variants
  - `[TODO]` exact Python title naming parity
  - `[TODO]` mixed-track default subtitle parity
  Required tests:
  - `[TODO]` track selection tests
  - `[TODO]` default subtitle tests
  - `[TODO]` title naming tests
  - `[TODO]` mixed-track integration tests

### Mux behavior

- `[PARTIAL]` MKV mux exists.
  Rust files:
  - [ffmpeg.rs](/Users/bvt/work/Cuentame/downloader/rust/src/ffmpeg.rs)
  Required tests:
  - `[TODO]` command-construction tests
  - `[TODO]` copy vs HEVC tests
  - `[TODO]` subtitle ordering/default tests

## CLI Compatibility

- `[PARTIAL]` Common Rust CLI exists.
  Rust files:
  - [cli.rs](/Users/bvt/work/Cuentame/downloader/rust/src/cli.rs)
  Remaining missing or incomplete flags:
  - `[TODO]` `--force-asr`
  - `[TODO]` `--es-postprocess`
  - `[TODO]` `--es-postprocess-force`
  - `[TODO]` `--es-postprocess-model`
  - `[TODO]` `--es-postprocess-chunk-cues`
  - `[TODO]` `--no-chunk`
  - `[TODO]` `--chunked`
  - `[TODO]` episode-level parallel controls
  - `[PARTIAL]` alignment flags exist only as a stub
  Existing Rust-only operational flags:
  - `[DONE]` `--save-auto-delay-asr-dir`
  Required tests:
  - `[TODO]` parser coverage for every flag
  - `[TODO]` workflow tests for every behavior-changing flag

## Supporting Systems

- `[TODO]` Global phrase cache
- `[TODO]` Telemetry
- `[PARTIAL]` prompt packaging cleanup

## Operational Cleanup

- `[PARTIAL]` structured logging is improved but still not fully stage-parity with Python.
  Rust files:
  - [logging.rs](/Users/bvt/work/Cuentame/downloader/rust/src/logging.rs)
  - [pipeline.rs](/Users/bvt/work/Cuentame/downloader/rust/src/pipeline.rs)

- `[TODO]` reduce remaining compiler warnings.

- `[BLOCKED]` decide whether translation remains sequential during stabilization.
  This is partly a product/reliability decision, not just an implementation task.

## Recommended Next Steps

1. Finish `--subtitle-align whisperx` with tests.
2. Add global phrase cache with tests.
3. Replace simple `index.html` with parity-oriented output and tests.
4. Finish ASR feature set, including `--force-asr`, with tests.
5. Implement ES post-processing with tests.
6. Add retry narrowing and fallback model retry with tests.
7. Add salvage/resume logic with tests.
8. Implement telemetry with tests.
9. Tighten cache/reset parity and media consistency checks with tests.
