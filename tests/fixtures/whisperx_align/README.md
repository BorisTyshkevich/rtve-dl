This fixture holds a short Spanish audio clip and matching SRT snippet for WhisperX alignment.

Expected files (stored via Git LFS):
- `sample_audio.wav` (mono 16kHz, ~3–6 minutes)
- `sample_es.srt` (matching subtitle snippet; timestamps start near 00:00:00)

To generate from a local MP4+VTT:
```
python tools/extract_alignment_fixture.py \
  --mp4 /path/to/episode.mp4 \
  --vtt /path/to/episode.es.vtt \
  --start 00:05:00 \
  --duration 00:03:00 \
  --out-dir tests/fixtures/whisperx_align
```

Smoke test:
```
python tools/whisperx_align_smoketest.py \
  --audio tests/fixtures/whisperx_align/sample_audio.wav \
  --srt tests/fixtures/whisperx_align/sample_es.srt \
  --out tmp/align_smoke.srt \
  --device mps
```
