# QuantForge

QuantForge is a reproducible quantitative research and event-driven backtesting
platform. It is intended for financial data engineering, signal research,
portfolio simulation, risk analysis, and out-of-sample evaluation.

QuantForge is a research and simulation project. It does not support live
trading.

## Current status

Milestone 1 provides the Python 3.12 package foundation and locked development
environment. Milestone 2 adds a standard-library ingestion module for monthly
BTCUSDT Spot 1h Binance Vision archives covering 2021 through 2025.

The ingestion module constructs deterministic URLs and paths, verifies SHA-256
checksums, validates ZIP and CSV structure, normalizes documented timestamp
units to UTC, publishes files atomically, and records provenance. Its tests use
only generated synthetic data.

Comprehensive market-data quality analysis, Parquet/DuckDB storage, signals,
backtesting, portfolio accounting, risk analytics, and research reporting are
planned and are not yet implemented.

## macOS setup

Install `uv` with Homebrew, install Python 3.12, and synchronize the locked
development environment:

```bash
brew install uv
uv python install 3.12
uv sync --locked --python 3.12 --group dev
```

## Quality checks

Run the test suite:

```bash
UV_OFFLINE=1 uv run --no-sync pytest -v
```

Run lint and formatting checks:

```bash
UV_OFFLINE=1 uv run --no-sync ruff check .
UV_OFFLINE=1 uv run --no-sync ruff format --check .
```

Build the source distribution and wheel:

```bash
uv build --offline
```

## Research scope

The initial planned research market is BTCUSDT Spot using 1-hour OHLCV data.
Binance Vision is only a tentative data source. Its licensing suitability and
the integrity of any acquired dataset must be verified before research use.
No real Binance data is included in this repository. Network-capable ingestion
must not be run until the dataset licensing gate receives explicit human
approval.

If Binance changes the checksum of an archive already accepted locally,
QuantForge reports a correction conflict and never overwrites it automatically.

## Historical ingestion safety

The downloader uses separate 10-second connection and 30-second read timeouts,
with at most three attempts and bounded exponential backoff for transient
failures. Downloads remain in temporary files until their SHA-256, ZIP
structure, CSV structure, and timestamps have been validated. Existing
archives are rehashed and revalidated before reuse.

Default safety limits are 16 MiB compressed, 64 MiB uncompressed, a 100:1
maximum compression ratio, and 1,000 CSV rows. A monthly 1-hour archive can
contain at most 744 normal hourly rows, so the row limit leaves headroom while
still bounding work. All limits are configurable. Compatibility with real
Binance archives remains unverified.

Each accepted archive has a deterministic JSON provenance sidecar containing
source URLs, dataset identity, expected and actual hashes, byte size, retrieval
time, timestamp unit, parser/schema versions, archive member name, the Dataset
Terms reference, and only HTTP metadata actually observed. Archive and sidecar
publication are individually atomic, but they are not a transactional pair. A
crash between them can leave an orphan archive, which later ingestion rejects
rather than inventing missing provenance.

The offline test suite generates all CSV and ZIP content in temporary
directories and uses injected transports; it does not contact Binance.

Binance Vision licensing remains under review. Public redistribution of the
datasets or derived reports has not been approved. QuantForge is not
production-ready and does not support live trading.

See `docs/architecture.md` for the intended architecture and current
implementation boundary.
