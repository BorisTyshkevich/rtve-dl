# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and a selector like `T7S5` (season 7, episode 5) or `T7` (whole season), it:

- downloads the video (prefers direct progressive MP4 when available)
- downloads Spanish (`es`) and English (`en`) subtitles when available
- muxes everything into an `.mkv` with up to 2 subtitle tracks (Spanish + English)

No translation features exist on `main`. The old experimental translation pipeline lives on the `experimental_translation` branch.

## Non-goals

- DRM circumvention (Widevine/FairPlay). This tool does not decrypt DRM. It only attempts direct media URLs that RTVE exposes.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH

## Install (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

Download one episode:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
```

Debug mode (prints stage progress and cache hits):

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame --debug
```

Russian subtitles (offline):

```bash
rtve_dl setup-argos
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
```

Prefer using RTVE's English subtitles for RU (en->ru). If RTVE has no English subtitles, you can optionally generate an
English track from Spanish (es->en) and then use that for RU:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame --translate-en-if-missing
```

Defaults:

- `--with-ru` is enabled by default (use `--no-with-ru` to disable)
- `--translate-en-if-missing` is enabled by default (use `--no-translate-en-if-missing` to disable)

Notes:

- `setup-argos` creates a dedicated `.venv_argos/` (Python 3.13) and installs Argos Translate + models.
  - We use Python 3.13 for `.venv_argos/` because Argos Translate's dependency stack is not reliable on Python 3.14+.
- Argos does not currently publish a direct `es->ru` model in its default index, so we install `es->en` and `en->ru`
  and let Argos pivot as needed.

Download a whole season:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 --series-slug cuentame
```

Quality selection:

- `--quality mp4` (default): prefer progressive MP4 URLs
- `--quality best`: best-effort fallback (may use HLS when needed)

## Caching / Idempotency

Re-running `download` is safe:

- output `.mkv` is skipped if it already exists
- cached `.mp4` is reused from `data/series/<slug>/tmp/`
- subtitle `.vtt` files are cached in `data/series/<slug>/subs/` and not re-downloaded

Project data is stored under:

`data/series/<series_slug>/`

and is ignored by git via `.gitignore`.

## Credits

RTVE link extraction is inspired by the Descargavideos project (`forestrf/Descargavideos`), specifically their RTVE handler logic.
See `NOTICE`.

## Legal

This software is provided "as-is" under the Apache-2.0 license. You are responsible for complying with RTVE terms and any applicable laws.
