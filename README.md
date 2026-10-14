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
Milestone 3 adds deterministic candle-quality assessment and verified monthly
Parquet publication using DuckDB.

The ingestion module constructs deterministic URLs and paths, verifies SHA-256
checksums, validates ZIP and CSV structure, normalizes documented timestamp
units to UTC, publishes files atomically, and records provenance. Its tests use
only generated synthetic data.

The quality layer detects missing, duplicate, out-of-order, misaligned, and
financially inconsistent candles without sorting or changing source
observations. Errors block storage; warnings such as zero volume or zero trades
are retained in the validation report. The storage layer independently
rechecks the raw archive and provenance, reparses and revalidates source rows,
writes a temporary Parquet partition, reads every value back, and only then
publishes the complete partition.

Signals, backtesting, portfolio accounting, risk analytics, and research
reporting are planned and are not yet implemented.

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

## Validated analytical storage

Financial values are checked before storage and written as DuckDB
`DECIMAL(38,18)` values without conversion through binary floating point.
Inputs declaring more than 18 fractional places are rejected rather than
having their scale coerced, including zero or trailing-zero values.
Timestamps are timezone-aware UTC `TIMESTAMPTZ` values and are compared to
microsecond precision after Parquet readback. Processed months use this
deterministic layout:

```text
data/processed/binance-vision/spot/klines/
  symbol=BTCUSDT/interval=1h/year=2024/month=01/
    BTCUSDT-1h-2024-01.parquet
    manifest.json
    validation.json
```

The manifest records source and provenance hashes, request identity, validation
and storage versions, DuckDB version, Parquet hash and size, row coverage, and
creation time. The validation report preserves every error and warning.
Reprocessing an unchanged, fully verified source reuses its partition.
Different source hashes or incompatible processing versions cause an explicit
conflict; existing output is never silently overwritten.

DuckDB can query a published partition directly:

```sql
SET TimeZone = 'UTC';
SELECT
  count(*),
  epoch_us(min(open_time)) AS first_open_time_us,
  epoch_us(max(open_time)) AS last_open_time_us,
  sum(volume)
FROM read_parquet(
  'data/processed/binance-vision/spot/klines/symbol=BTCUSDT/interval=1h/year=2024/month=01/BTCUSDT-1h-2024-01.parquet',
  hive_partitioning = false
);
```

The writer verifies timestamp columns as `TIMESTAMPTZ` and compares their
integer epoch-microsecond values during readback. This avoids a hidden runtime
dependency on `pytz`, which DuckDB's Python conversion of returned
`TIMESTAMPTZ` objects may otherwise require. Very large sums can overflow
`DECIMAL(38,18)` even when each input fits; DuckDB reports that overflow
explicitly, so research queries must handle it rather than expect silent
rounding.

The test suite uses complete synthetic calendar months, including leap years
and the 2024-to-2025 timestamp-unit boundary. No live-data compatibility has
been established. Directory publication uses a same-filesystem rename and
filesystem synchronization where available, but this is not a claim of perfect
crash durability on every filesystem. The source files are rechecked before
publication, and the final directory is reopened and verified before success is
returned. Concurrent cooperating writers rely on the operating system refusing
to rename over an existing nonempty partition; the supported macOS behavior is
tested. This does not protect against malicious filesystem modification or
changes that occur and are reverted between integrity checks.

Binance Vision licensing remains under review. Public redistribution of the
datasets or derived reports has not been approved. QuantForge is not
production-ready and does not support live trading.

See `docs/architecture.md` for the intended architecture and current
implementation boundary.
