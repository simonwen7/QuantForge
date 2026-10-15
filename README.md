# QuantForge — Deterministic Research Simulation Platform

A local research simulator for causal trading signals, event-driven execution,
and Decimal portfolio accounting, with a synthetic research terminal on top.

QuantForge is a Python library that replays hourly candles in time order,
turns a fixed rule into a signal only after that candle is complete, simulates
a spot fill one hour later, and records cash and bitcoin with `Decimal`. The
same inputs produce the same report id. It is a research and simulation
project. It does not place live orders.

## Web experience

`apps/web` is a Next.js terminal for the same engine. It has six workspaces:
Market, Lab, Performance, Replay, Compare, and System. The page loads a
synthetic momentum run from the Python API and draws that response. It does
not ship sample prices or sample performance.

Every terminal path is labeled `synthetic_unverified`. The candles are generated
for June 2022 from a seed. They are not BTCUSDT history. Sharpe, drawdown, and
fills on screen are properties of that generated path. The M9 protocol stays
unapproved. Acceptance and activation stay disabled.

The browser calls `/api/*` on the Next.js origin. A server route forwards that
call to the FastAPI app. Locally the API is `http://127.0.0.1:8000`. A
production deploy reads `QUANTFORGE_API_ORIGIN` at request time. The build does
not need that variable, and a production process without it does not call
localhost.

Deployment is prepared and not performed. There is no public URL yet. The
two-project runbook is `docs/web-deployment.md`.

```bash
uv sync --locked --python 3.12 --group dev --group api
uv run --group api --no-sync uvicorn api.index:app --host 127.0.0.1 --port 8000
```

```bash
cd apps/web
npm ci
npm run dev
```

API routes, all under `/api` and also mounted without that prefix:

| Method | Path | Role |
| --- | --- | --- |
| GET | `/api/health` | Process check |
| GET | `/api/config` | Bounds, cost presets, and the synthetic statement |
| POST | `/api/backtest` | One strategy, cash, and buy-and-hold, with the event trace |
| POST | `/api/compare` | Both rules at every cost preset, without per-cell traces |
| GET | `/api/research-block` | The blocked empirical study, with no path and no reader |

Screenshots are not checked in. Capture them from a local or deployed session
after the API has returned a real run. Do not draw stand-in charts for the
repository.

## Overview

Market backtests are easy to get wrong: a signal can see a future price, a
fill can be booked at a price that was not yet known, and a binary float can
change a fee by a fraction of a cent. QuantForge keeps those steps in separate
modules and tests them with synthetic candles. Real exchange archives are a
later, separate decision. They are not required to run the demo or the tests.

## Core Engineering Capabilities

- Checksummed ZIP and CSV ingestion, with provenance and conflict detection
  when an already accepted checksum changes.
- Candle validation that reports gaps, duplicates, and inconsistent prices
  without rewriting the source rows.
- Monthly Parquet partitions written and read back through DuckDB, using
  `DECIMAL(38,18)` rather than binary floats.
- Causal signals. A 24-hour momentum rule and a fixed mean-reversion rule see
  only closes that are already complete.
- A single-threaded event clock. The open, a due fill, the prior close, and
  the new order stay in a defined order. The simulated fill waits one hour.
- A fee-aware cash and bitcoin ledger. Buys reserve the fee before sizing.
- Content-hashed experiment, run, and report identifiers. The wall clock is
  not part of those hashes.
- Risk reports and a chronological fixed-rule evaluation. Folds do not inherit
  positions, and the sensitivity grid does not pick a winner.
- Fail-closed study scopes. Train, validation, and the final holdout are
  denied unless a future human authorization exists. None exists now.

Three ways to describe the project:

- Built a deterministic event-driven backtester with Decimal portfolio
  accounting, latency-aware execution, and content-hashed run identities.
- Implemented checksummed archive ingestion, candle validation, and
  DuckDB/Parquet storage with provenance and conflict checks.
- Developed chronological research evaluation, reproducible risk reports, and
  fail-closed study access controls, verified through a substantial synthetic
  test suite.

## Architecture Summary

```text
Synthetic bars -> causal signals -> latency-one replay -> Decimal ledger -> report
```

That path runs locally. A second path, from archive acquisition through
SHA-256 checks, validation, and DuckDB/Parquet, stops at a readiness gate.
Legal review, real-file verification, protocol approval, and partition
authorization are open. The gate is an application check, not a tamper-proof
lock on the filesystem.

The picture and the module boundaries are in `docs/architecture.md`.

## Tech Stack

Python 3.12, uv, DuckDB, Parquet, pytest, and Ruff. The terminal adds
Next.js, TypeScript, and FastAPI. A GitHub Actions workflow is configured for
Ubuntu, Python 3.12, and Node.js 22. A hosted run of the original library job
was reported after M10. The web job added with the terminal has not been
observed on GitHub by this change. There is no status badge.

The library runtime dependency is DuckDB. pytest and Ruff are development
tools. FastAPI, Pydantic, and Uvicorn are in the `api` dependency group and
are not installed with the library wheel. Hatchling builds the package and is
not imported by the application.

## Quick Start

Install uv, then, from the repository root and with network access:

```bash
uv sync --locked --python 3.12 --group dev --group api
```

That step downloads the locked packages, including the API group used by the
terminal tests. It is not an empty-cache offline
install. Later test commands can stay offline.

## One-Minute Synthetic Demo

```bash
uv run --no-sync python -m quantforge.analytics /tmp/quantforge-demo
```

This writes `synthetic-study.json` and `synthetic-study.md` for a 32-bar
in-memory fixture. The command prints a report id. Running it again on the
same inputs reprints that id and the same file bytes. The report's opening
statement says it is a synthetic mechanics demonstration and not BTCUSDT
performance evidence. Any return, Sharpe, or drawdown figure in that file is
a property of the fixture. It is not a market result and not an exchange fill.

`python -m quantforge.analytics` with no path still writes
`artifacts/m7-synthetic/` in the current directory. Prefer the explicit path.
The default directory is gitignored.

The demo does not open a 2024 validation file or the 2025 holdout.

## Testing and CI

After the environment exists:

```bash
UV_OFFLINE=1 uv run --no-sync pytest
UV_OFFLINE=1 uv run --no-sync ruff check .
UV_OFFLINE=1 uv run --no-sync ruff format --check .
uv lock --check
uv build
```

`uv build` needs Hatchling in the uv cache or permission to resolve it. Tests
and Ruff do not. The pytest suite builds its own ZIP and CSV fixtures and does
not call Binance.

`.github/workflows/ci.yml` runs those checks on Ubuntu for pulls and for
pushes to `main`, then installs the built wheel in a separate virtual
environment and runs the demo outside the checkout. A second job installs the
locked Next.js app, typechecks it, lints it, runs its unit tests, and builds
it. The frontend build does not need `QUANTFORGE_API_ORIGIN`.

From `apps/web`, the local frontend checks are:

```bash
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

The latest full local run before this milestone, M9 acceptance, collected 291
tests and passed them. This milestone's full suite, clean-wheel install, and
hosted Actions result are checked in the acceptance phase, not by the presence
of the workflow file.

## Reproducibility and Research Boundaries

Identifiers hash canonical JSON. Approval state and the clock stay outside the
protocol hash. The candidate protocol is pending human approval. Creating the
hash does not approve it.

The following remain unresolved or unused:

- Third-party market-data licensing.
- Compatibility with a real Binance archive.
- Human approval of the research protocol.
- Train, validation, and final-holdout performance studies.
- The 2025 holdout, which the demo and the default loaders do not open.
- Live trading, which is not implemented.

Synthetic statistics do not show that a rule made money. Local scope checks
protect the supported workflow. A person who can read files or edit the code
can bypass them.

`docs/research/m9-readiness.md` records the blocked readiness report, which
contains no performance figures.

## Documentation

- `docs/architecture.md` — pipeline, storage, execution, readiness, and the terminal.
- `docs/development.md` — setup, tests, packaging, and module imports.
- `docs/web-deployment.md` — two Vercel projects. No public URL is recorded there.
- `docs/research/m5-momentum-hypothesis.md` — the fixed momentum rule.
- `docs/research/m8-mean-reversion-walk-forward.md` — the fixed walk-forward.
- `docs/research/m9-readiness.md` — authorization and the holdout gate.
