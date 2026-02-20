# Subtitle Timing And Alignment

This document explains how subtitle timing is computed and applied for ES/EN/RU tracks.

## Sources And Files

- ES source:
  - RTVE VTT if present, otherwise ASR fallback.
  - Built file: `tmp/<slug>/srt/<base>.spa.srt`
- ES aligned:
  - When alignment enabled, WhisperX retimes ES and writes
    `tmp/<slug>/srt/<base>.spa.aligned.srt`
- EN source:
  - RTVE VTT if present, otherwise ES->EN MT.
  - Built file: `tmp/<slug>/srt/<base>.eng.srt`
- RU/refs source:
  - ES->RU MT (and refs) built from ES cues.

## Delay Modes

Only two modes exist:

- `--subtitle-delay auto` (default)
- `--subtitle-delay <ms>` (manual)

No per-series cache is used; auto-delay is computed per episode when needed.

## Auto-Delay Computation (Per Episode)

When auto-delay is required and `spa.srt` did not already exist at the start
of the episode run, we compute a delay:

1. Energy alignment: estimate delay by correlating ES cue activity with
   audio energy (ffmpeg extraction).
2. If energy confidence is low, run ASR on a 5-minute clip from the middle
   of the episode:
   - Extract WAV clip with ffmpeg.
   - Transcribe clip with MLX or WhisperX. WhisperX auto-delay always forces `compute_type=int8`,
     regardless of `--asr-compute-type` (CLI still applies to other ASR uses).
   - Match ASR cues to ES cues inside the clip window.
   - Compute median delta as delay (ms).

The computed delay is logged:

```
subtitle delay computed (episode): <ms>
```

If `spa.srt` already exists at episode start, auto-delay is skipped and no
recomputation occurs.

## How Delay Is Applied

### Alignment Off

If `--subtitle-align off`:

- ES cues are shifted by the computed delay and written to `spa.srt`.
- EN VTT cues are shifted by the same delay before writing `eng.srt`.
- EN/RU/refs MT are built from the (shifted) ES cues, so they inherit timing.
- MKV mux delay is set to `0`.

Result: all SRT files are already aligned to audio without mux offsets.

### Alignment On (WhisperX)

If `--subtitle-align whisperx`:

- ES cues are pre-shifted by the computed delay.
- WhisperX retimes ES to audio and writes `spa.aligned.srt`.
- `spa.aligned.srt` is the only ES track muxed.
- EN VTT cues are shifted by the same delay before writing `eng.srt`.
- EN/RU/refs MT are built from the aligned ES cues, so they inherit timing.
- MKV mux delay is set to `0`.

Result: ES timing comes from WhisperX, all other tracks follow the same base
timing (global delay or aligned cues).

## When Resolve Calls Are Skipped

Resolve (episode metadata HTTP calls) is skipped if both are already local:

- `mp4` exists and is valid
- `spa.srt` exists and is non-empty

In that case:

- ES is read directly from `spa.srt`
- EN VTT is not downloaded; MT fallback is used if EN is required

## Key Properties

- Auto-delay is applied to files, not mux offsets.
- Alignment is per-cue retiming, not a single delay value.
- EN VTT is shifted by the same delay to match ES timing.
