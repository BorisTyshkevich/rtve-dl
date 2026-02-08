# Contributing

## Principles

- No DRM circumvention code.
- Prefer stable, explicit formats (JSONL, TSV) and reproducible pipelines.
- Keep link-resolution logic well-isolated and well-attributed.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Reporting issues

When filing a bug report, include:

- Series URL and selector (`T7` / `T7S5`)
- The `asset_id` if known
- Whether subtitles exist on RTVE for that asset
- The failing URL or API response snippet (redact personal data)

