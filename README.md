# rtve-dl

RTVE.es downloader that produces an `.mkv` with embedded subtitles:

- Spanish original subtitles (downloaded from RTVE if available)
- English subtitle track (downloaded from RTVE if available)
- Spanish subtitles with Russian learning annotations (3 tracks): `A1+`, `A2+`, `B1+`
- Russian full translation subtitle track (single, natural translation)

This project is intended for legitimate users of RTVE content (for example, living in Spain with normal access).

## Non-goals

- DRM circumvention (Widevine/FairPlay). `--ignore-drm` does **not** decrypt DRM; it only enables a best-effort
  download if RTVE still exposes a direct progressive MP4 for the same `asset_id`.

## Requirements

- Python 3.10+
- `ffmpeg` on PATH
- No mandatory third-party Python dependencies (stdlib-only core)
  - Optional: `cryptography` is used only by a legacy Descargavideos-compat fallback module (`ztnr/res`), which is not
    required for the normal thumbnail-based resolver.

## Install (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

Index (subtitles only) to build a per-series dataset and translation queue:

```bash
rtve_dl index "https://www.rtve.es/play/videos/cuentame-como-paso/"
# prints a <series_slug> you will use below
```

You can also limit indexing to a single season or episode:

```bash
rtve_dl index "https://www.rtve.es/play/videos/cuentame-como-paso/" --selector T7
rtve_dl index "https://www.rtve.es/play/videos/cuentame-como-paso/" --selector T7S5
```

Mine frequent phrase candidates (idiom-like multiword expressions) from already-indexed Spanish cues:

```bash
rtve_dl mine-phrases "https://www.rtve.es/play/videos/cuentame-como-paso/" --series-slug cuentame --selector T7 --min-count 10
```

Review/export mined phrases and re-mine cleanly:

```bash
rtve_dl phrases-export "<series_slug>" --min-count 10
rtve_dl phrases-clear "<series_slug>"
rtve_dl mine-phrases "<series_slug>" --selector T7 --min-count 10
```

Build subtitles only (works even when RTVE video is DRM-protected):

```bash
rtve_dl subs "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
# or, after indexing:
rtve_dl subs "<series_slug>" T7S5
```

Download and build one episode or a full season:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7 --series-slug cuentame
# or, after indexing:
rtve_dl download "<series_slug>" T7S5
rtve_dl download "<series_slug>" T7
```

If RTVE marks an episode as DRM, `download` will fail by default.
If you want a best-effort attempt (without DRM circumvention), you can enable:

```bash
rtve_dl download "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame --ignore-drm
```

Note: for some RTVE MP4 URLs, downloads work only when the request includes a browser-like `Referer`. `rtve_dl download`
sets `Referer: https://www.rtve.es/` when fetching progressive MP4s via `ffmpeg`.

## Caching / Idempotency

Re-running commands is safe:

- `index` does not re-download existing `.vtt` files and avoids double-counting terms when re-indexing.
- `download` skips an episode if the output `.mkv` already exists; it also reuses an existing cached `.mp4` when present.

## Subtitle levels and tracks

We generate three Spanish learning subtitle tracks with Russian glosses in parentheses:

- `A1+`: include glosses for terms tagged `A2` and above
- `A2+`: include glosses for terms tagged `B1` and above
- `B1+`: include glosses for terms tagged `B2` and above

Glosses are single Russian lemmas (singular is fine).

## Per-series dataset

All frequency counts, contexts, and lexicons are stored per series:

`data/series/<series_slug>/`

- `lexicon_words.tsv`: word -> CEFR + Russian gloss (editable)
- `lexicon_phrases.tsv`: phrase/idiom -> CEFR + Russian gloss (editable)
- `stopwords_es.txt`: always-skip tokens (editable)
- `cache.sqlite3`: extracted term counts + example contexts + translation status (generated)

If you edit `lexicon_words.tsv` / `lexicon_phrases.tsv` manually, apply changes into the DB with:

```bash
rtve_dl lexicon-sync "<series_slug>"
```

## Batch Codex run (translation + CEFR labeling)

This repo intentionally does not hardcode a translation provider. Instead it exports JSONL tasks that you can feed to your
"batch codex run" workflow, then re-import the resulting JSONL back into the dataset.

Prompts live in:

- `prompts/gloss_cefr_ru.md`
- `prompts/translate_full_ru.md`

Commands:

```bash
rtve_dl export-gloss "<series_slug>"
rtve_dl import-gloss "<series_slug>" results_gloss.jsonl

rtve_dl export-ru "<series_slug>"
rtve_dl import-ru "<series_slug>" results_ru.jsonl
```

Then build with the Russian full-translation track enforced:

```bash
rtve_dl download "<series_slug>" T7S5 --with-ru
rtve_dl download "<series_slug>" T7S5 --require-ru
```

If the RTVE episode is DRM-protected, `download` will fail. You can still mux subtitles with your own local legal video file:

```bash
rtve_dl mux-local "<series_slug>" T7S5 --video /path/to/video.mp4
```

If you already have a direct media URL and the appropriate cookies (for example from your own authenticated session),
you can download via `curl` and mux in one step without implementing token generation:

```bash
rtve_dl mux-url "<series_slug>" T7S5 --url "https://...mp4?...download-token=..." --cookie-file cookies.txt --header "Referer: https://www.rtve.es/"
```

Security note: do not paste session cookies or tokens into issue trackers or chat logs. Use `--cookie-file`.

Tip: if your URL contains `download-token=...` and it fails, `mux-url` will automatically retry after removing the `download-token` parameter.

## Minimal Descargavideos-Style Media Test

The Descargavideos RTVE handler relies primarily on extracting media URLs from RTVE's `thumbnail` PNG metadata.
This repo includes a tiny Python port you can use as a smoke test to download a single RTVE MP4 by `asset_id`:

```bash
python3 tools/dv_rtve_one.py "https://www.rtve.es/play/videos/.../880355/" --range 0-1048575
```

By default it downloads only the first 1MiB (range request). Pass `--range ""` to download the full file.

## Credits

RTVE link extraction is inspired by the Descargavideos project (`forestrf/Descargavideos`), specifically their RTVE handler logic.
See `NOTICE`.

## Legal

This software is provided "as-is" under the Apache-2.0 license. You are responsible for complying with RTVE terms and any applicable laws.
