# ASR Fixture

This fixture is the first 5 minutes of cached episode `S08E19_con_la_iglesia_hemos_topado`.

Files:
- `s08e19_first5m.flac`: mono `16 kHz` audio fixture for native ASR tests
- `s08e19_first5m.reference.srt`: reference Spanish subtitle text trimmed to the same 5-minute window

Regenerate from cached local media:

```bash
./scripts/extract_asr_fixture.sh \
  tmp/cuentameT8/mp4/S08E19_con_la_iglesia_hemos_topado.mp4 \
  tmp/cuentameT8/srt/S08E19_con_la_iglesia_hemos_topado.spa.srt \
  tests/fixtures/asr/s08e19_first5m.flac \
  tests/fixtures/asr/s08e19_first5m.reference.srt
```

The Rust acceptance test compares normalized transcript text against the normalized reference subtitle text and currently requires at least `0.60` similarity.
