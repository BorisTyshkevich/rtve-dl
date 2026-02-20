# WhisperX Alignment on Apple Silicon (MPS)

This guide covers alignment-only use of WhisperX (retiming subtitles to audio) on Apple Silicon with MPS.

## Requirements

- macOS 12.3+ and Apple Silicon
- Python 3.10+
- Xcode command line tools: `xcode-select --install`
- PyTorch with MPS enabled

Check MPS availability:

```python
import torch
print(torch.backends.mps.is_available())
```

Reference: https://docs.pytorch.org/docs/stable/notes/mps.html

## Install WhisperX

In the repo:

```bash
python -m pip install -e '.[asr-whisperx]'
```

## Alignment-only usage

Retimes ES subtitles to audio without re-transcribing:

```bash
rtve_dl "https://www.rtve.es/play/videos/cuentame-como-paso/" T8S1 \
  -s cuentameT8 --subtitle-align whisperx --subtitle-align-device mps
```

Device modes:
- `mps`: require MPS; fail if unavailable
- `auto`: use MPS if available, otherwise CPU
- `cpu`: force CPU

## MPS caveats (WhisperX)

WhisperX MPS has known issues and may crash in some workloads. See:
https://github.com/m-bain/whisperX/issues/109

If alignment fails on MPS, rerun with:

```bash
--subtitle-align-device cpu
```

## CTranslate2 notes (transcription only)

Alignment-only does not use CTranslate2. If you plan to use WhisperX transcription on Apple Silicon:

- MPS is not supported in CTranslate2 (`unsupported device mps`).
  https://github.com/OpenNMT/CTranslate2/issues/1562

- For optimized CPU builds, compile from source with Apple Accelerate:
  - `-DWITH_ACCELERATE=ON`
  https://opennmt.net/CTranslate2/installation.html

## Smoke test fixture

Generate a 3–6 minute audio + SRT snippet and run the smoke test:

```bash
python tools/extract_alignment_fixture.py \
  --mp4 /path/to/episode.mp4 \
  --vtt /path/to/episode.es.vtt \
  --start 00:05:00 \
  --duration 00:03:00 \
  --out-dir tests/fixtures/whisperx_align

python tools/whisperx_align_smoketest.py \
  --audio tests/fixtures/whisperx_align/sample_audio.wav \
  --srt tests/fixtures/whisperx_align/sample_es.srt \
  --out tmp/align_smoke.srt \
  --device mps
```
