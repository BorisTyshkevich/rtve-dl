# Changelog

## 0.2.0

- Reworked downloader pipeline for high parallelism:
  - parallel episode workers for season runs
  - parallel subtitle/video stages per episode
  - parallel Codex chunk execution
  - compact per-episode debug progress states
- Added resilient media download behavior:
  - resumable MP4 downloads via `.partial.mp4`
  - safer retry handling and improved downloader logging
  - stronger local cache checks before network/resolve
- Added and stabilized subtitle generation fallbacks:
  - WhisperX fallback path for missing RTVE Spanish subtitles
  - MLX Whisper backend (Apple Silicon friendly) as primary ASR path
  - MLX model fallback (`whisper-small` -> `whisper-tiny`)
  - VAD configuration options and platform guidance updates
- Added subtitle timing controls:
  - manual subtitle offset at MKV mux stage
  - automatic subtitle delay estimation with cache and refresh control
- Added catalog and resolve optimizations:
  - RTVE catalog disk cache
  - pre-resolve short-circuit when local episode assets are complete
- Refined output/index UX:
  - richer `index.html` generation with metadata enrichment
  - JS-generated M3U links for player launch fallback
  - improved episode card/title/link behavior
- Simplified and hardened storage/caching model:
  - canonical layout: `data/<slug>/` outputs and `tmp/<slug>/` caches
  - cache reset controls via `--reset-layer`:
    `subs-es`, `subs-en`, `subs-ru`, `subs-refs`, `video`, `mkv`, `catalog`
  - reset dependency expansion and selector-scoped invalidation
- Added detailed cache internals documentation in `caches.md`
  and streamlined `README.md` around user-facing commands.

## 0.1.0

- Initial project scaffold.
- Per-series indexing: downloads ES/EN subtitles when available and extracts term frequencies.
- JSONL export/import for CEFR+gloss tasks and full Russian cue translation tasks.
- MKV mux workflow with Spanish, English (if available), three RU-gloss Spanish tracks, and full Russian translation track.
