# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and selector (`T7S5` or `T7`), it downloads video/subtitles, builds Russian tracks via Codex, and muxes everything to MKV.

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
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
```

Download whole season:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 --series-slug cuentame
```

Debug + parallel (parallel is enabled by default):

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 --series-slug cuentame --debug --parallel
```

## Codex Chunk Concurrency

`--jobs-codex-chunks` controls how many Codex chunk requests run in parallel inside one translation task.

- `1`: sequential chunks (more stable, lower chance of auth/rate-limit storms)
- `2-4`: faster, but higher risk of chunk failures under account limits

Example (stable mode):

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S2 \
  --series-slug cuentameT8 --jobs-codex-chunks 1
```

## Subtitle Delay

Default subtitle delay is `800ms` and is applied at MKV mux stage only.

Manual delay:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 \
  --series-slug cuentame --subtitle-delay-ms 1200
```

Auto delay:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 \
  --series-slug cuentame --subtitle-delay-mode auto
```

Force recompute auto delay cache:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 \
  --series-slug cuentame --subtitle-delay-mode auto --subtitle-delay-auto-refresh
```

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

Examples:

Rebuild MKV only:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  --series-slug cuentame --reset-layer mkv
```

Rebuild refs subtitles and remux:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  --series-slug cuentame --reset-layer subs-refs
```

Re-download video and rebuild dependent layers:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  --series-slug cuentame --reset-layer video
```

Refresh catalog cache:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 \
  --series-slug cuentame --reset-layer catalog
```

You can pass multiple layers:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S9 \
  --series-slug cuentame --reset-layer subs-ru,subs-refs
```

If a season run with reset crashes, restart without `--reset-layer` to continue from already rebuilt cache.

## Force ASR Mode

Use `--force-asr` to generate ASR-based subtitles even when RTVE provides Spanish subtitles.
This creates a parallel set of translations from the ASR source, useful for comparing ASR vs RTVE quality.

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  --series-slug cuentameT8 --force-asr
```

In force-asr mode:
- ASR subtitles are always generated and translated
- RTVE-based translations are NOT regenerated (saves API costs)
- Cached RTVE translations from previous runs are included if they exist
- Default subtitle track is `ES+RU refs/ASR`

Track naming:
- Normal mode: `{model} MT` for translations, `ES+RU refs` (default)
- Force-ASR mode: `{model} MT/ASR` for ASR-based translations, `ES+RU refs/ASR` (default)

## ASR Fallback

If RTVE has no ES subtitles and `--asr-if-missing` is enabled (default), ASR generates `*.spa.srt`.

After ASR, Spanish subtitles are post-processed by Codex (light normalization) by default:
- fixes obvious ASR mistakes
- normalizes punctuation/capitalization/accents
- preserves meaning (no heavy rewrites)

Controls:
- `--no-es-postprocess` to disable
- `--es-postprocess-force` to run cleanup even for RTVE-provided ES subtitles
- `--es-postprocess-model <model>` to override model only for ES cleanup
  (default cleanup model: `gpt-5.1-codex-mini`)
- `--es-postprocess-chunk-cues <N>` to override chunk size only for ES cleanup
  (default is 100 cues per cleanup chunk)

If ES cleanup fails, pipeline falls back to raw ES subtitles and continues.

`--reset-layer subs-es` removes cleaned ES (`*.spa.srt`) and cleanup caches, but keeps raw ASR cache
(`*.spa.asr_raw.srt`) so ES can be rebuilt without re-running ASR.
If you need a full re-ASR, delete `tmp/<slug>/srt/<base>.spa.asr_raw.srt` manually before rerun.

Use MLX backend explicitly:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 \
  --series-slug cuentame --asr-backend mlx
```

Use WhisperX backend:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 \
  --series-slug cuentame --asr-backend whisperx
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
