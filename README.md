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
- default backend: `whisperx`
- optional backend: `mlx`

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


## Translation 

Default backend for translation is Claude (`--translation-backend claude`), but can be switched to codex.  Both of them should be preconfigured to run with auth or KEY.

### No-Chunk Mode (Default for Claude)

Claude no-chunk mode sends all cues in one request:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 -s cuentameT8
```

Although Claude models may support very large context windows (for example 200K and, in some variants, up to 1M), large real-world subtitle payloads are often less reliable in single-request translation. Common failure modes are missing IDs or partially structured output.

To keep runs stable, `rtve_dl` automatically disables no-chunk mode and switches to chunked mode when input is larger than `1000` cues.

Benefits of no-chunk (when it works well):
- Better full-episode consistency
- Simpler cache shape
- No chunk boundary effects

### Chunked Mode (Default for Codex)

Chunked mode splits cues into batches and validates outputs per chunk:

```bash
# assumes RTVE_SERIES_URL/RTVE_SERIES_SLUG are set (see above)
rtve_dl T8S1 --translation-backend codex
```

Override defaults:
- `--chunked` - Force chunked mode (even with Claude)
- `--no-chunk` - Force single-request mode (even with Codex).  
  Note: for very large inputs (`>1000` cues), the pipeline still switches to chunked mode for reliability.
- `--codex-chunk-cues N` - Set chunk size (default: 500)
- `--jobs-codex-chunks N` - Parallel chunk workers (default: 4)

### Chunk Concurrency

When using chunked mode, `--jobs-codex-chunks` controls parallel chunk requests:

- `1`: sequential chunks (more stable, lower chance of auth/rate-limit storms)
- `2-4`: faster, but higher risk of chunk failures under account limits

Example (stable chunked mode):

```bash
rtve_dl T8S2 --chunked --jobs-codex-chunks 1
```

## Subtitle Alignment

Sometimes source subtitles can have delay or get earlier than audio. It could be same per the whole episode or differs from cue to cue.  

There are two settings --subtitle-delay and --subtitle-align to solve two different timing problems.

  - --subtitle-delay auto:
      - applies one global shift to all cues (e.g., +1200 ms)
      - good when the whole subtitle track is uniformly early/late
      - cheap and fast (run ASR on 5 min fragment to align)
  - --subtitle-align whisperx:
      - retimes cues to audio boundaries per segment
      - good when drift is non-uniform across the episode
      - heavier/slower, may help more but not always

Default subtitle delay is `auto` (calculating from source audio stream) and is applied by shifting timestamps in subtitle files.

### Manual delay:

```bash
rtve_dl T7S5 --subtitle-delay 1200
```

### Subtitle Alignment

Optional WhisperX alignment retimes ES subtitles to audio without re-transcribing:

```bash
rtve_dl T8S1 --subtitle-align whisperx --subtitle-align-device mps
```

Note: WhisperX alignment is experimental and may not improve timing for all episodes. mpx mode (GPU) can produce wrong results.

Flags:
- `--subtitle-align off|whisperx` (default: off)
- `--subtitle-align-device auto|mps|cpu` (default: auto)
- `--subtitle-align-model <name>` (optional override)

Setup guide for Apple Silicon MPS: `docs/whisperx_mps_setup.md`.

## HEVC Encoding (Optional)

By default MKV mux keeps source video/audio bitstreams (`copy` mode).  
To re-encode video to H.265/HEVC:

```bash
rtve_dl T8S1 --video-codec hevc
```

HEVC flags:
- `--video-codec copy|hevc` (default: `copy`)
- `--hevc-device cpu|gpu|auto` (default: `cpu`)
- `--hevc-crf N` (default: `18`)
- `--hevc-preset <x265 preset>` (default: `slow`)

Recommended for quality-first compression:

```bash
rtve_dl T8S1 --video-codec hevc --hevc-device cpu --hevc-crf 18 --hevc-preset slow
```

Apple Silicon note:
- `--hevc-device gpu` uses `hevc_videotoolbox`.
- On some Apple Silicon setups this path can be inconsistent for size/quality tradeoff (for example, larger files than expected at similar visual quality).
- If your goal is smaller files while preserving visual quality, prefer `--hevc-device cpu` (`libx265`).

## Subtitle tracks in resulting MKV

By default rtve_dl produces 5 tracks:
- es
- en
- ru
- ru-dual (es+ru)
- ru-refs (only complicated B1+ words/phrases translated)

The player (like VLC) opens ru-refs subtitle track. You can change it by `--default-subtitle` option.

Disabling tracks in MKV:

- `--sub <track>=<off|on|require>` (repeatable)
- Tracks: `es`, `en`, `ru`, `ru-dual`, `refs`
- Defaults: `es=on`, `en=on`, `ru=require`, `ru-dual=on`, `refs=on`
- `--default-subtitle es|en|ru|ru-dual|refs` (default: refs)
- Legacy track flags (`--en`, `--ru`, `--ru-refs`) are replaced by `--sub`.

Examples:
- Disable refs only: `--sub refs=off`
- RU-only: `--sub es=off --sub en=off --sub ru=on --sub ru-dual=off --sub refs=off --default-subtitle ru`

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

## Unsorted Notes

- Re-running rtve_dl is safe and cache-based.
- Global static phrase cache is loaded from `data/global_phrase_cache.json` (if present).
  Start from `global_phrase_cache.example.json`.
- Codex chunk telemetry is written to `tmp/<slug>/meta/telemetry.sqlite`.
- For ASR episodes, raw ES subtitles are kept as `tmp/<slug>/srt/<base>.spa.asr_raw.srt`.
- Telemetry is written into sql-lite:
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
