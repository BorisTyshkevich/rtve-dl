# rtve-dl

Downloader and subtitle-processing project for RTVE episodes, with Python as the current production implementation and Rust as the in-repo rewrite target.

The repository now contains parallel implementations:

| Implementation | Status | Main doc |
|---|---|---|
| Python | Current working implementation | `README.python.md` |
| Rust | Partial pipeline with native-Whisper migration in progress | `README.rust.md` |

## What the project does

Given an RTVE series URL and selector (`T7S5` or `T7`), the project can:
- resolve episode media and subtitle sources
- download or reuse local video
- build Spanish subtitles from RTVE VTT or ASR fallback
- build translated subtitle tracks
- mux final MKV outputs

Typical output lives under:
- `tmp/<slug>/...` for cache and work artifacts
- `data/<slug>/...` for final outputs

## Implementations

### Python

The Python implementation is the current reference and supports the real pipeline today:
- RTVE resolution and downloading
- subtitle processing
- translation orchestration
- ASR fallback
- MKV muxing

See `README.python.md` for setup, CLI usage, and operational notes.

### Rust

The Rust implementation has been added in parallel and is not feature-complete yet, but it is beyond scaffold stage.

Current decisions for the Rust rewrite:
- keep `ffmpeg` and `ffprobe` as external binaries
- keep Python code intact during migration
- use Rust-native/local ASR for transcription
- treat alignment as a separate backend concern

Current Rust ASR behavior:
- default `--asr-backend auto`
- prefers native Whisper when a local model is installed
- falls back to `whisperx` if no native model is found

Native model installation and lookup are documented in `README.rust.md`.

See `README.rust.md` for the current Rust pipeline status and migration direction.

## Shared runtime dependencies

Depending on implementation and enabled features, you may need:
- `ffmpeg`
- `ffprobe`
- translation backend CLI access such as `codex` or `claude`

Python-specific and Rust-specific setup instructions are intentionally kept out of this file and live in their implementation READMEs.

## Repository guide

- Python CLI and pipeline: `src/rtve_dl`
- Rust rewrite scaffold: `rust/`
- Python architecture notes: `docs/architecture.md`
- Cache behavior: `caches.md`

## Migration policy

- Python source remains in the repository and should not be removed during the Rust rewrite.
- Rust work should be additive until feature parity is proven.
- Root `README.md` stays universal and implementation-agnostic.

## License

Apache-2.0.
