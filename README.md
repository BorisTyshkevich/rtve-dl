# rtve-dl

Simple RTVE.es downloader for legitimate users.

Given a series URL and a selector like `T7S5` (season 7, episode 5) or `T7` (whole season), it:

- downloads the video (prefers direct progressive MP4 when available)
- downloads Spanish (`es`) and English (`en`) subtitles when available
- if Spanish subtitles are missing, WhisperX can generate Spanish subtitles from audio (enabled by default)
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
- `whisperx` CLI on PATH for ES ASR fallback when RTVE has no subtitles
  - Install via the `asr` extra (`pip install -e '.[asr]'`) or separately.

### Python Compatibility For WhisperX

WhisperX dependency resolution is currently sensitive to Python version.

- Recommended for ASR fallback: Python `3.12` or `3.13`
- Not recommended for WhisperX right now: Python `3.14` (common resolver failures around `ctranslate2`/version pins)

If your current venv uses Python 3.14, create a dedicated venv for ASR runs:

```bash
python3.13 -m venv .venv313
source .venv313/bin/activate
pip install -U pip setuptools wheel
pip install -e '.[asr]'
```

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

Optional WhisperX fallback dependencies:

```bash
pip install -e '.[asr]'
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

- `--asr-if-missing` is enabled by default (use `--no-asr-if-missing` to disable)
- `--translate-en-if-missing` is enabled by default (use `--no-translate-en-if-missing` to disable)
- `--with-ru` is enabled by default (use `--no-with-ru` to disable)
- `--require-ru` is enabled by default (use `--no-require-ru` to allow episodes without RU)
- `--codex-chunk-cues` defaults to `400`

### Spanish ASR fallback (WhisperX)

If RTVE provides no Spanish subtitles for an episode and `--asr-if-missing` is enabled, the downloader runs WhisperX on the cached MP4 and generates:

- `SxxExx_<title>.spa.srt` ... Spanish subtitles generated from audio

This fallback is automatic and only triggers when RTVE ES subtitles are missing.
If RTVE ES subtitles exist, they are used directly and WhisperX is skipped.

Default ASR settings are conservative for compatibility:

- `--asr-device cpu`
- `--asr-compute-type float32`
- `--asr-model large-v3`
- `--asr-vad-method silero`

Note for Apple Silicon:

- You can try `--asr-device mps --asr-compute-type float16`, but many WhisperX builds
  still route through faster-whisper/ctranslate2 where `mps` is unsupported.
- `rtve_dl` now auto-retries with `cpu/float32` if WhisperX returns `unsupported device mps`.

You can tune performance/quality explicitly:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 --series-slug cuentame \
  --asr-model large-v3 --asr-device cpu --asr-compute-type float32 --asr-vad-method silero --asr-batch-size 8
```

#### Recommended installation and first run (Apple Silicon)

1. Create and activate a Python 3.13 virtual environment:

```bash
python3.13 -m venv .venv313
source .venv313/bin/activate
```

2. Install the project with ASR extras:

```bash
pip install -U pip setuptools wheel
pip install -e '.[asr]'
```

3. Validate WhisperX is available:

```bash
whisperx --help
```

4. Test ASR-only pipeline on an episode with missing RTVE subtitles (no RU/EN translation):

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 \
  --series-slug cuentame \
  --debug \
  --no-with-ru \
  --no-translate-en-if-missing
```

5. Run full pipeline after ASR validation:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S12 --series-slug cuentame --debug
```

#### Operational notes

- ASR uses the cached episode MP4 in `data/series/<slug>/tmp/`.
- Generated Spanish SRT is cached (`*.spa.srt`) and reused on reruns.
- If you want to force re-transcription, delete only the target `*.spa.srt` and rerun.
- If `--with-ru` is enabled (default), RU and bilingual tracks are generated from the resulting Spanish cues (RTVE ES or ASR ES).

#### Troubleshooting

- `whisperx: command not found`
  - Activate the venv where WhisperX is installed, or install with `pip install -e '.[asr]'`.
- `Could not find a version that satisfies ... ctranslate2==...` / Python resolver errors
  - Switch to Python 3.12/3.13 venv and reinstall.
- `Weights only load failed` / `omegaconf.listconfig.ListConfig` in WhisperX log
  - Use `--asr-vad-method silero` (now default) to avoid Pyannote model loading path.
  - `rtve_dl` also runs WhisperX with `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` automatically.
  - If you still hit it, reinstall ASR deps in the same venv and rerun:

```bash
pip install -U -e '.[asr]'
```
- Slow transcription
  - Use `--asr-model base` for faster but lower-quality output.
  - Lower `--asr-batch-size` if memory pressure appears.
- MPS/device issues
  - Try `--asr-device cpu --asr-compute-type float32` explicitly.
  - If you requested `mps`, `rtve_dl` will auto-retry CPU when WhisperX reports `unsupported device mps`.

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
- ASR-generated Spanish `.srt` files are cached in `data/series/<slug>/tmp/` and reused
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
