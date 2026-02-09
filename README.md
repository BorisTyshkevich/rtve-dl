# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and a selector like `T7S5` (season 7, episode 5) or `T7` (whole season), it:

- downloads the video (prefers direct progressive MP4 when available)
- downloads Spanish (`es`) and English (`en`) subtitles when available
- if English subtitles are missing, it can translate Spanish -> English via Codex (enabled by default)
- muxes everything into an `.mkv` with subtitle tracks:
  - Spanish (RTVE)
  - English (RTVE if available, otherwise machine-translated if enabled)
  - Russian (machine translation via `codex exec`, enabled by default)
  - Spanish|Russian bilingual (two-line subtitle: Spanish then Russian, enabled by default)
 
The old experimental translation pipeline (lexicon datasets, multiple learning tracks, etc.) lives on the `experimental_translation` branch.

## Non-goals

- DRM circumvention (Widevine/FairPlay). This tool does not decrypt DRM. It only attempts direct media URLs that RTVE exposes.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH
- `codex` CLI on PATH (for Russian subtitles and optional ES->EN fallback)
  - You must be logged in / have credentials configured for non-interactive use.

## Install (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional (only if you want to use the Descargavideos-compatible crypto helpers in
`rtve_dl.rtve.descargavideos_compat`):

```bash
pip install -e '.[dv]'
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

Tune Codex chunk size (smaller chunks are slower but more robust; larger chunks mean fewer Codex calls):

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame --codex-chunk-cues 800
```

Defaults:

- `--translate-en-if-missing` is enabled by default (use `--no-translate-en-if-missing` to disable)
- `--with-ru` is enabled by default (use `--no-with-ru` to disable)
- `--require-ru` is enabled by default (use `--no-require-ru` to allow episodes without RU)
- `--codex-chunk-cues` defaults to `400`

### How RU translation works

We translate **Spanish -> Russian** cue-by-cue using `codex exec` in JSONL chunks.

For each episode we create cached files under:

`data/series/<series_slug>/tmp/`

- `SxxExx_<title>.ru.c<chunk>.ru.in.0001.jsonl` ... input chunks
- `SxxExx_<title>.ru.c<chunk>.ru.out.0001.jsonl` ... output chunks (Codex last message)
- `SxxExx_<title>.rus.srt` ... Russian subtitle track
- `SxxExx_<title>.spa_rus.srt` ... bilingual Spanish|Russian track (two lines per cue)

If a chunk output exists, we reuse it. If you want to force regeneration, delete the corresponding cached file(s) and rerun.

### English fallback translation

If RTVE doesn't provide English subtitles for an episode and `--translate-en-if-missing` is enabled, we generate:

- `SxxExx_<title>.en.c<chunk>.en.in.0001.jsonl` / `...out...` ... Codex cache
- `SxxExx_<title>.eng.srt` ... English subtitle track (labeled `English (MT)` inside MKV)

### Regenerating outputs

Everything is cache-based and idempotent. The simplest way to rebuild the final `.mkv` is:

- delete `data/series/<slug>/out/<episode>.mkv`
- rerun the same `rtve_dl download ...` command

It will reuse the cached `.mp4` and `.vtt`/`.srt` files unless you delete those too.

### Season behavior

For a season selector like `T7`:

- if an episode fails RU generation and `--require-ru` is on, we log an error and continue to the next episode
- the command exits non-zero if any episode failed

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
- RU chunk files (`*.ru.c*.ru.in.*.jsonl`, `*.ru.c*.ru.out.*.jsonl`) and built subtitle tracks (`*.rus.srt`, `*.spa_rus.srt`) are reused when present

Project data is stored under:

`data/series/<series_slug>/`

and is ignored by git via `.gitignore`.

## Notes

- Why does debug show many `videos.json?page=N` requests?
  - That is just RTVE’s paginated series catalog API; we walk pages until we have enough metadata to resolve the requested selector (episode or season).
- Why use `ffmpeg` to download?
  - RTVE assets may be served as MP4 or as HLS (`.m3u8`). `ffmpeg` handles both consistently, and we also use it to mux subtitles into MKV.

## Credits

RTVE link extraction is inspired by the Descargavideos project (`forestrf/Descargavideos`), specifically their RTVE handler logic.
See `NOTICE`.

## Legal

This software is provided "as-is" under the Apache-2.0 license. You are responsible for complying with RTVE terms and any applicable laws.
