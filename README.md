# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and selector (`T7S5` or `T7`), it downloads video/subtitles, builds Russian tracks via Codex or Claude, and muxes everything to MKV.

## What it produces

Per episode MKV can include:
- Spanish subtitle track
- English subtitle track (RTVE or ES->EN fallback)
- Russian full translation track
- Spanish|RU refs learning track
- Spanish|Russian (Full) dual-line track

`data/<slug>/index.html` is regenerated after each run.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH
- `codex` CLI on PATH (authenticated for non-interactive `codex exec`)
- or `claude` CLI on PATH

Codex translation defaults:
- primary model: `gpt-5.1-codex-mini`
- fallback model (failed chunks only): `gpt-5.3-codex`
- chunk size: `500` cues (`ru_refs` is internally capped to `200`)

Optional ASR fallback when ES subtitles are missing:
- default backend: `mlx-whisper`
- optional backend: `whisperx`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

With default ASR backend:

```bash
pip install -e '.[asr]'
```

With WhisperX backend:

```bash
pip install -e '.[asr-whisperx]'
```

## Quick Start

Download one episode:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 -s cuentame
```

Download whole season:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 -s cuentame
```

Debug + parallel (parallel is enabled by default):

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 -s cuentame -d
```

### Using Environment Variables

Set series URL and slug once, then just specify the selector:

```bash
export RTVE_SERIES_URL="https://www.rtve.es/play/videos/cuentame-como-paso/"
export RTVE_SERIES_SLUG="cuentame"

rtve_dl T7S5        # Download episode
rtve_dl T7          # Download season
rtve_dl T8S1 -d     # Different season with debug
```

### Short Flags

| Short | Long | Description |
|-------|------|-------------|
| `-s` | `--series-slug` | Series slug for output directories |
| `-d` | `--debug` | Enable debug output |
| `-j` | `--jobs-episodes` | Episode parallelism |
| `-m` | `--model` | Translation model (auto-routes to backend) |


## Translation Backend

Default backend is Claude (`--translation-backend claude`).

### No-Chunk Mode (Default for Claude)

Claude's large context window (200K tokens) allows translating entire episodes in a single request:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 -s cuentameT8
```

Benefits:
- Better translation consistency (model sees full episode context)
- Simpler caching (one file per track)
- No chunk boundary artifacts

### Chunked Mode (Default for Codex)

For smaller context models, chunking splits cues into batches:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  -s cuentameT8 --translation-backend codex
```

Override defaults:
- `--chunked` - Force chunked mode (even with Claude)
- `--no-chunk` - Force single-request mode (even with Codex)
- `--codex-chunk-cues N` - Set chunk size (default: 500)
- `--jobs-codex-chunks N` - Parallel chunk workers (default: 4)

### Chunk Concurrency

When using chunked mode, `--jobs-codex-chunks` controls parallel chunk requests:

- `1`: sequential chunks (more stable, lower chance of auth/rate-limit storms)
- `2-4`: faster, but higher risk of chunk failures under account limits

Example (stable chunked mode):

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S2 \
  -s cuentameT8 --chunked --jobs-codex-chunks 1
```

## Subtitle Delay

Default subtitle delay is `auto` and is applied by shifting subtitle files (mux delay remains 0).

Manual delay:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 \
  -s cuentame --subtitle-delay 1200
```

Auto delay:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 \
  -s cuentame --subtitle-delay auto
```

## Subtitle Alignment

Optional WhisperX alignment retimes ES subtitles to audio without re-transcribing:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  -s cuentameT8 --subtitle-align whisperx --subtitle-align-device mps
```

Note: WhisperX alignment is experimental and may not improve timing for all episodes.

Flags:
- `--subtitle-align off|whisperx` (default: off)
- `--subtitle-align-device auto|mps|cpu` (default: auto)
- `--subtitle-align-model <name>` (optional override)

Subtitle tracks:
- `--sub <track>=<off|on|require>` (repeatable)
- Tracks: `es`, `en`, `ru`, `ru-dual`, `refs`
- Defaults: `es=on`, `en=on`, `ru=require`, `ru-dual=on`, `refs=on`
- `--default-subtitle es|en|ru|ru-dual|refs` (default: refs)
- Legacy track flags (`--en`, `--ru`, `--ru-refs`) are replaced by `--sub`.

Examples:
- Disable refs only: `--sub refs=off`
- ES-only: `--sub en=off --sub ru=off --sub ru-dual=off --sub refs=off`
- No ES output track but keep RU: `--sub es=off --sub ru=on`
`--default-subtitle` is strict: if selected stream is unavailable, the run fails.

Note: When subtitle alignment is enabled, any auto/manual delay is applied as a pre-shift before alignment and mux delay is set to 0. Auto delay is computed per episode only when ES subtitles are rebuilt (i.e., `spa.srt` is missing at episode start). The pre-shifted `spa.srt` is kept for review and `spa.aligned.srt` is the only ES track muxed. When alignment is off, `spa.srt` is muxed.

ES-only (skip EN/RU translations):
```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  -s cuentameT8 --sub en=off --sub ru=off --sub ru-dual=off --sub refs=off --default-subtitle es
```

Setup guide for Apple Silicon MPS: `docs/whisperx_mps_setup.md`.

## Reset Layers

Use `--reset-layer` to invalidate selected cache layers before processing.
Reset is applied in a selector-wide preflight phase first (for all episodes in `T7`, or one episode in `T7S9`), then recomputation starts.

Allowed values:
- `subs-es`
- `subs-en`
- `subs-ru`
- `subs-refs`
- `video`
- `mkv`
- `catalog`

Catalog cache TTL is 7 days by default (stored in `tmp/<slug>/meta/catalog_<hash>.json`).

Examples:

Rebuild MKV only:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  -s cuentame --reset-layer mkv
```

Rebuild refs subtitles and remux:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  -s cuentame --reset-layer subs-refs
```

Re-download video and rebuild dependent layers:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  -s cuentame --reset-layer video
```

Refresh catalog cache:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 \
  -s cuentame --reset-layer catalog
```

You can pass multiple layers:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  -s cuentame --reset-layer subs-ru,subs-refs
```

If a season run with reset crashes, restart without `--reset-layer` to continue from already rebuilt cache.

## Force ASR Mode

Use `--force-asr` to generate ASR-based subtitles even when RTVE provides Spanish subtitles.
This creates a parallel set of translations from the ASR source, useful for comparing ASR vs RTVE quality.

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  -s cuentameT8 --force-asr
```

In force-asr mode:
- ASR subtitles are always generated and translated
- RTVE-based translations are NOT regenerated (saves API costs)
- Cached RTVE translations from previous runs are included if they exist
- Default subtitle track follows `--default-subtitle` (default `refs`)

Track naming:
- Normal mode: `{model} MT` for translations, `ES+RU refs`
- Force-ASR mode: `{model} MT/ASR` for ASR-based translations, `ES+RU refs/ASR`

## ASR Fallback

If RTVE has no ES subtitles and `--asr-if-missing` is enabled (default), ASR generates `*.spa.srt`.

After ASR, Spanish subtitles are post-processed by Codex (light normalization) by default:
- fixes obvious ASR mistakes
- normalizes punctuation/capitalization/accents
- preserves meaning (no heavy rewrites)
- uses episode-level context from RTVE catalog description (when available)
  to disambiguate unclear ASR wording; context is injected once per prompt
  (not repeated per cue row)

Controls:
- `--no-es-postprocess` to disable
- `--es-postprocess-force` to run cleanup even for RTVE-provided ES subtitles
- `--es-postprocess-model <model>` to override model only for ES cleanup
  (default cleanup model: `gpt-5.1-codex-mini`)
- `--es-postprocess-chunk-cues <N>` to override chunk size only for ES cleanup
  (default is 100 cues per cleanup chunk)

If ES cleanup fails, pipeline falls back to raw ES subtitles and continues.

`--reset-layer subs-es` removes cleaned ES (`*.spa.srt`) and raw ASR cache (`*.spa.asr_raw.srt`).
Rebuilding ES after reset will re-run ASR if RTVE subtitles are missing.

Default ASR backend is WhisperX (CPU, model `small`, compute type `int8`). Note: auto-delay forces `int8` regardless of `--asr-compute-type`. Use MLX backend explicitly:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 \
  -s cuentame --asr-backend mlx
```

Use WhisperX backend explicitly:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 \
  -s cuentame --asr-backend whisperx
```

## Notes

- Re-running `download` is safe and cache-based.
- Global static phrase cache is loaded from `data/global_phrase_cache.json` (if present).
  Start from `global_phrase_cache.example.json`.
- Codex chunk telemetry is written to `tmp/<slug>/meta/telemetry.sqlite`.
- For ASR episodes, raw ES subtitles are kept as `tmp/<slug>/srt/<base>.spa.asr_raw.srt`.
- SQL artifacts:
  - schema bootstrap: `src/rtve_dl/sql/schema.sql`
  - analytics queries: `src/rtve_dl/sql/reports.sql`
- The tool does not bypass DRM.
- Full cache internals and reset semantics are documented in `caches.md`.
- Project structure map: `docs/architecture.md`.

## License

Apache-2.0.

References:
- RTVE Play: https://www.rtve.es/play/
- Descargavideos tool inspiration: https://www.descargavideos.tv/
