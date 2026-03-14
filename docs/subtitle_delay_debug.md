# Subtitle Delay Debugging

Use these tools when subtitle auto-delay looks wrong and you want to inspect the delay calculation directly from a local `mp4` + `spa.srt` pair.

They do not require a full downloader run.

For exact parity with production, prefer the cached RTVE ES VTT input if it exists. Production computes auto-delay from fresh RTVE VTT cues before writing `spa.srt`.

## What Each Tool Covers

- Python tool: [tools/inspect_subtitle_delay.py](/Users/bvt/work/Cuentame/downloader/tools/inspect_subtitle_delay.py)
  - Uses the Python delay estimator in [src/rtve_dl/subs/delay_auto.py](/Users/bvt/work/Cuentame/downloader/src/rtve_dl/subs/delay_auto.py)
  - Supports Python ASR backends: `whisperx` and `mlx`
- Rust tool: [inspect_subtitle_delay.rs](/Users/bvt/work/Cuentame/downloader/rust/src/bin/inspect_subtitle_delay.rs)
  - Uses the Rust delay estimator in [rust/src/delay.rs](/Users/bvt/work/Cuentame/downloader/rust/src/delay.rs)
  - Defaults to Rust `auto`, which now resolves to WhisperX
  - Still supports explicit `whisper-rs`

## Output Fields

Both tools print:

- `energy`: delay estimate from audio energy correlation
- `asr`: delay estimate from subtitle-to-ASR text matching
- `final`: the estimate the pipeline would use

If energy confidence is low, the estimator falls back to ASR.

## Python Tool

Basic usage:

```bash
python tools/inspect_subtitle_delay.py \
  --mp4 tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt tmp/cuentameT8/vtt/881402.es.vtt \
  --debug
```

Save the ASR transcript used for delay matching:

```bash
python tools/inspect_subtitle_delay.py \
  --mp4 tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --save-asr-srt tmp/debug/python-whisperx-delay.srt \
  --debug
```

ASR-only:

```bash
python tools/inspect_subtitle_delay.py \
  --mp4 tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --srt tmp/cuentameT8/srt/S08E20_m_s_dura_ser_la_ca_da.spa.srt \
  --skip-energy \
  --asr-backend whisperx \
  --debug
```

MLX example:

```bash
python tools/inspect_subtitle_delay.py \
  --mp4 tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --srt tmp/cuentameT8/srt/S08E20_m_s_dura_ser_la_ca_da.spa.srt \
  --skip-energy \
  --asr-backend mlx \
  --debug
```

Notes:

- The Python tool does not use `whisper-rs`.
- Temporary ASR files default to `tmp/subtitle-delay-inspect/`.

## Rust Tool

Build and run from the `rust/` directory:

```bash
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt ../tmp/cuentameT8/vtt/881402.es.vtt \
  --debug
```

Run the Rust tool with its default `auto` backend:

```bash
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt ../tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --save-asr-srt ../tmp/debug/rust-auto-delay.srt \
  --debug
```

Run only the Rust `whisper-rs` ASR path:

```bash
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt ../tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --asr-backend whisper-rs \
  --asr-model small \
  --debug
```

Run Rust with WhisperX instead:

```bash
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt ../tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --asr-backend whisperx \
  --asr-model small \
  --asr-device cpu \
  --debug
```

Notes:

- Temporary ASR files default to `tmp/subtitle-delay-inspect-rust/` relative to `rust/`.
- `whisper-rs` requires a local model such as `models/ggml-small.bin`.

## Diffing Backend Transcripts

To compare the exact transcripts used by delay matching:

```bash
python tools/inspect_subtitle_delay.py \
  --mp4 tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --save-asr-srt tmp/debug/python-whisperx-delay.srt \
  --debug

cd rust
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --vtt ../tmp/cuentameT8/vtt/881402.es.vtt \
  --skip-energy \
  --asr-backend whisper-rs \
  --save-asr-srt ../tmp/debug/rust-whisper-rs-delay.srt \
  --debug
cd ..

diff -u tmp/debug/python-whisperx-delay.srt tmp/debug/rust-whisper-rs-delay.srt | less
```

## Recommended Debug Flow

1. Run the Python tool with `--skip-asr` to see whether energy returns anything useful.
2. Run Python ASR-only if you want to compare WhisperX or MLX behavior.
3. Run the Rust tool with `--skip-energy` to inspect the default Rust downloader path.
4. Run the Rust tool with `--skip-energy --asr-backend whisper-rs` only when you want to compare native Whisper separately.
5. Compare the printed `final` delay with the manual value.
6. If debug is enabled, inspect lines like:

```text
subtitle auto-delay ASR cluster: delay_ms=560 matched=12/19 cluster_ratio=0.84
```

That line shows which delta cluster won after fuzzy text matching.

## Full Pipeline Validation

The standalone tools do not depend on `--reset-layer`. They work directly on existing local files.

For a full downloader validation run, remember:

- auto-delay is skipped if the ES subtitle file already exists at episode start
- to force recomputation during a normal run, reset the ES subtitle layer first

Example:

```bash
RTVE_SERIES_URL="https://www.rtve.es/play/videos/cuentame-como-paso/" \
RTVE_SERIES_SLUG="cuentameT8" \
rtve_dl T8S20 -d --reset-layer subs-es \
  --save-auto-delay-asr-dir tmp/debug/auto-delay
```
