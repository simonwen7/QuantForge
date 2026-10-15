# Development

QuantForge targets Python 3.12 only. Install [uv](https://docs.astral.sh/uv/)
first. On macOS, `brew install uv` is enough. The commands below then match
Ubuntu and macOS.

## Environment

From the repository root, with network access:

```bash
uv sync --locked --python 3.12 --group dev
```

That installs the locked runtime and development packages into `.venv`. A fresh
machine cannot complete this step with an empty cache and the network disabled.
DuckDB, pytest, and ruff come from `uv.lock`. Hatchling is the build backend
in `pyproject.toml`. It is not a runtime dependency and is not listed in the
lockfile. `uv build` resolves it into the uv cache. An earlier online build, or
a cache that already contains Hatchling, can then run `uv build --offline`. An
empty cache with the network disabled fails at that step. That is a bootstrap
limit, not a defect in the package source.

After the environment exists, tests and lint do not need the network:

```bash
UV_OFFLINE=1 uv run --no-sync pytest
UV_OFFLINE=1 uv run --no-sync ruff check .
UV_OFFLINE=1 uv run --no-sync ruff format --check .
```

A single test file:

```bash
UV_OFFLINE=1 uv run --no-sync pytest tests/analytics/test_report.py
```

Build the source distribution and wheel when Hatchling can be resolved:

```bash
uv build
```

The wheel contains `quantforge` only. It does not contain tests, docs,
`data/`, or generated reports.

## Wheel smoke test

Use a virtual environment that is not `.venv`, and run it from a directory
that is not the checkout, so Python does not import `src/` through the working
directory or `PYTHONPATH`.

```bash
uv venv --python 3.12 /tmp/quantforge-smoke
uv pip install --python /tmp/quantforge-smoke/bin/python dist/quantforge-0.1.0-py3-none-any.whl
cd /tmp
unset PYTHONPATH
/tmp/quantforge-smoke/bin/python -c "import quantforge.analytics as analytics; assert 'site-packages' in analytics.__file__"
/tmp/quantforge-smoke/bin/python -m quantforge.analytics /tmp/quantforge-demo
```

The current project version is `0.1.0`. If that version changes, install the
wheel filename Hatchling actually wrote under `dist/`.

## Synthetic demo

Prefer an explicit directory:

```bash
uv run --no-sync python -m quantforge.analytics /tmp/quantforge-demo
```

`python -m quantforge.analytics` with no argument still writes
`artifacts/m7-synthetic/` relative to the current directory. That directory is
gitignored at the repository root. Both forms use the same in-memory fixture.
They do not download market data. Run either command twice and compare the
report id and the two files. They should match byte for byte.

The optional readiness report is a short Python call, documented in
`docs/research/m9-readiness.md`. It does not calculate performance.

## Modules

| Path | Responsibility |
| --- | --- |
| `quantforge.ingestion` | URL, checksum, ZIP, and CSV validation |
| `quantforge.quality` | Candle checks that do not alter source rows |
| `quantforge.storage` | In-memory DuckDB and monthly Parquet publication |
| `quantforge.research` | Returns, statistics, partitions, and experiment ids |
| `quantforge.strategies` | Fixed momentum and mean-reversion signals |
| `quantforge.backtest` | Event order, fills, and the Decimal ledger |
| `quantforge.analytics` | Synthetic risk report and `python -m` demo |

Import M8 and M9 from their modules: `quantforge.analytics.walk_forward_report`,
`quantforge.analytics.final_report`, `quantforge.analytics.verified_study`,
`quantforge.research.protocol_freeze`, `quantforge.research.access`,
`quantforge.research.readiness`, and `quantforge.research.boundary`. The
package roots do not re-export them. Doing so created a circular import.

## Data and outputs

Do not commit root `data/raw`, `data/interim`, `data/processed`, `data/cache`,
or `artifacts/`. Automated tests build ZIP and CSV fixtures in pytest temporary
directories and replace the HTTP transport. They do not contact Binance and do
not read a 2024 or 2025 market file.

Writers take a directory argument. Point them at `/tmp` or a pytest `tmp_path`,
not at `data/raw` or `data/processed`.

## Web terminal

The API dependency group is installed with the dev group:

```bash
uv sync --locked --python 3.12 --group dev --group api
uv run --group api --no-sync uvicorn api.index:app --host 127.0.0.1 --port 8000
```

The Next.js app lives in `apps/web`. `npm ci` then `npm run dev`. Leave
`QUANTFORGE_API_ORIGIN` unset during `next dev`; the route falls back to
`http://127.0.0.1:8000`. Production does not use that fallback. See
`docs/web-deployment.md`.

Frontend checks from `apps/web`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

`npm run build` must succeed without a production API origin.

## Troubleshooting

`uv sync --locked` fails offline on a new machine because the lockfile
packages are not in the cache yet. Allow network access once, then use
`UV_OFFLINE=1` for pytest and ruff.

`uv build --offline` fails with Hatchling missing from the cache for the same
reason. Run `uv build` with network access, or reuse a cache from a machine
that has already resolved Hatchling. Do not add Hatchling to the runtime
dependencies to hide that.

`python -m quantforge.analytics` inside the checkout writes under
`artifacts/`. Pass a directory when you want the files somewhere else.

If `import quantforge` resolves to the checkout instead of an installed wheel,
the current directory or `PYTHONPATH` is ahead of site-packages. Change
directory and unset `PYTHONPATH` before the smoke import.
