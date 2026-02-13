# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and selector (`T7S5` or `T7`), it downloads video/subtitles, builds Russian tracks via Codex, and muxes everything to MKV.

## What it produces

Per episode MKV can include:
- Spanish subtitle track
- English subtitle track (RTVE or ES->EN fallback)
- Russian full translation track
- Spanish|RU refs learning track

`data/<slug>/index.html` is regenerated after each run.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH
- `codex` CLI on PATH (authenticated for non-interactive `codex exec`)

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

## ASR Fallback

If RTVE has no ES subtitles and `--asr-if-missing` is enabled (default), ASR generates `*.spa.srt`.

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
- The tool does not bypass DRM.
- Full cache internals and reset semantics are documented in `caches.md`.

## License

Apache-2.0.

References:
- RTVE Play: https://www.rtve.es/play/
- Descargavideos tool inspiration: https://www.descargavideos.tv/
