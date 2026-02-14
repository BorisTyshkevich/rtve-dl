# Architecture

## Repository Layout

```text
.
├── src/
│   └── rtve_dl/
│       ├── cli.py                  # CLI entrypoint
│       ├── workflows/
│       │   └── download.py         # Main orchestration pipeline
│       ├── rtve/                   # RTVE catalog/resolve/download helpers
│       ├── subs/                   # VTT/SRT parsing, rendering, delay estimation
│       ├── codex_*.py              # Codex chunk translation pipelines
│       ├── asr_*.py                # ASR backends
│       ├── ffmpeg.py               # Download/mux helpers
│       ├── telemetry.py            # SQLite telemetry writes
│       ├── tmp_layout.py           # tmp/<slug>/... structure and migration
│       ├── prompts/                # Prompt templates used by codex modules
│       └── sql/                    # Packaged SQL resources (part of runtime)
│           ├── schema.sql          # Telemetry DB schema bootstrap
│           ├── reports.sql         # Report query pack
│           └── migrations/         # Reserved for future SQL migrations
├── tools/                          # Utility scripts for local ops/testing
├── docs/                           # Project documentation
├── README.md                       # User-facing usage guide
├── caches.md                       # Cache internals and reset semantics
├── data/                           # Runtime output (mkv, index.html)
└── tmp/                            # Runtime cache/work artifacts
```

## Why `src/rtve_dl`

The project uses Python `src` layout to keep import/runtime boundaries explicit and avoid accidental imports from repo root during local runs.

## Why `src/rtve_dl/sql`

`schema.sql` and `reports.sql` are runtime assets, so they live inside the package and are loaded via `importlib.resources`. This keeps SQL and code versioned together and consistent for installed environments.

## Runtime Storage (outside source tree)

- `tmp/<slug>/...` keeps transient and cache artifacts.
- `data/<slug>/...` keeps final output files.

Source code never writes runtime state into `src/`.
