# rtve-dl (Rust)

Rust rewrite of the `rtve-dl` pipeline, added in parallel with the existing Python implementation.

The Rust code is intended to preserve the same high-level workflow:
- resolve RTVE episodes
- download or reuse media
- build Spanish subtitles from RTVE VTT or local ASR fallback
- build translated subtitle tracks
- mux final MKV output

The Python implementation remains the reference implementation during migration.

## Status

Current state:
- RTVE catalog and asset resolution are implemented
- MP4 download and MKV mux are implemented
- VTT -> SRT conversion is implemented
- subtitle auto-delay is implemented
- translation and ASR parity are still incomplete

Short-term design decisions:
- keep `ffmpeg` and `ffprobe` as external binaries
- keep Python implementation intact
- prefer WhisperX for ASR in the Rust implementation
- keep alignment as a separate concern from transcription

## Requirements

- Rust toolchain (`rustc`, `cargo`)
- `ffmpeg` on PATH
- `ffprobe` on PATH
- translation backend CLI access (`codex`, `claude`, and/or `gemini`)
- `whisperx` on PATH if you use the default Rust ASR mode
- local ASR model files only if you explicitly use `--asr-backend whisper-rs`
- on Apple Silicon, Xcode Command Line Tools are recommended for Metal builds

## Build

From the repo root:

```bash
cargo run --manifest-path rust/Cargo.toml -- --help
```

Or from the Rust directory:

```bash
cd rust
cargo run -- --help
```

Or use the repo wrapper:

```bash
./rtve --help
```

## Native Whisper Models

The Rust pipeline now defaults to:

```bash
--asr-backend auto
```

`auto` means:
- use external `whisperx`
- use `--asr-backend whisper-rs` explicitly if you want native Rust Whisper

Recognized model lookup:
- explicit file path passed via `--asr-model /path/to/model.bin`
- `WHISPER_MODEL_PATH=/path/to/model.bin`
- `WHISPER_MODEL_DIR=/path/to/models`
- local default locations such as `models/`, `tmp/models/`, `~/.cache/whisper/`, `~/.cache/whisper.cpp/`, `~/models/`

If `--asr-model small` is used, Rust will look for files like:
- `models/ggml-small.bin`
- `models/ggml-small.gguf`
- `~/.cache/whisper/ggml-small.bin`

### Install a model

From the repo root:

```bash
curl -L https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/models/download-ggml-model.sh -o /tmp/download-ggml-model.sh
bash /tmp/download-ggml-model.sh tiny models
bash /tmp/download-ggml-model.sh small models
```

That creates, for example:
- `models/ggml-tiny.bin`
- `models/ggml-small.bin`

Recommended:
- `small` for better subtitle-delay quality
- `tiny` only for quick experiments

### Force backend selection

Use native Whisper explicitly:

```bash
./rtve --asr-backend whisper-rs --asr-model small -s cuentameT8 --debug T8S20
```

On Apple Silicon, the native `whisper-rs` backend is built with the `metal` feature enabled in this repo, so that path can use Apple's GPU through Metal.

Use external WhisperX explicitly:

```bash
./rtve --asr-backend whisperx --asr-model small -s cuentameT8 --debug T8S20
```

Save the ASR SRT used by subtitle auto-delay during a normal Rust run:

```bash
./rtve --asr-backend auto --subtitle-delay auto \
  --save-auto-delay-asr-dir tmp/debug/auto-delay \
  -s cuentameT8 --debug T8S20
```

That directory will receive files like:
- `S08E20_m_s_dura_ser_la_ca_da.auto_delay.whisperx.srt`

Test native Metal-backed delay estimation directly:

```bash
cd rust
cargo run --bin inspect_subtitle_delay -- \
  --mp4 ../tmp/cuentameT8/mp4/S08E20_m_s_dura_ser_la_ca_da.mp4 \
  --srt ../tmp/cuentameT8/srt/S08E20_m_s_dura_ser_la_ca_da.spa.srt \
  --skip-energy \
  --asr-backend whisper-rs \
  --asr-model small \
  --save-asr-srt ../tmp/debug/rust-whisper-rs-metal-delay.srt \
  --debug
```

## CLI

The Rust CLI accepts the same broad invocation shape as the Python tool:

```bash
./rtve "https://www.rtve.es/play/videos/cuentame-como-paso/" T7S5 --series-slug cuentame
```

Example using cached media with the default WhisperX auto-selection:

```bash
./rtve -s cuentameT8 --default-subtitle es --reset subs-es --sub en=off --sub ru=off --sub ru-dual=off --sub refs=off --debug T8S20
```

Common ASR-related flags:
- `--asr-backend auto|whisperx|whisper-rs`
- `--asr-model small` or `--asr-model /absolute/path/to/model.bin`
- `--subtitle-delay auto`
- `--save-auto-delay-asr-dir <dir>`

Translation flags:
- `--translation-backend claude|codex|gemini`
- `--claude-model sonnet`
- `--codex-model gpt-5.1-codex-mini`
- `--gemini-model gemini-2.5-pro` (optional; if omitted, Gemini CLI chooses its default)
- `-m, --model` overrides the backend-specific model flag for any translation backend

## Planned backend model

### Media

The Rust implementation will call external tools rather than embedding FFmpeg libraries:
- `ffmpeg` for download/remux/mux/audio extraction
- `ffprobe` for duration and stream inspection

This keeps behavior aligned with the Python implementation and avoids libav binding complexity.

### ASR

Current Rust ASR direction:
- transcription: external `whisperx` by default
- optional explicit native backend: `whisper-rs`
- alignment: separate optional backend, likely external rather than embedded

This mirrors the current project reality where transcription and alignment are different subsystems.

## Migration rules

- Do not modify or remove Python source while porting features.
- Keep cache and output layout compatible where possible.
- Treat Python outputs as the behavior baseline for Rust parity.

## Related docs

- Universal repo guide: `README.md`
- Python implementation guide: `README.python.md`
- Current Python architecture: `docs/architecture.md`

## License

Apache-2.0.
