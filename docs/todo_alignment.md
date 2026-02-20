# TODO: Alignment Fixture Improvements

- Provide a scripted, reproducible way to generate a small alignment fixture from a local MP4+VTT.
- Add a safety check that refuses to commit media files to git (pre-commit or CI).
- Add a tiny synthetic audio option (TTS + SRT) for public repos without copyrighted material.
- Document recommended clip length and language coverage for more stable alignment tests.
- Add a test that skips gracefully when `sample_audio.wav` is missing, with a clear message.
