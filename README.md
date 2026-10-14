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
Parquet publication using DuckDB. Milestone 4 adds verified research loading,
Decimal close-to-close returns, trailing descriptive statistics, a fixed
chronological train/validation/holdout protocol, and deterministic experiment
identifiers. Milestone 5 adds one fixed long-or-flat momentum signal. It does
not create orders, fills, or performance results.

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

Research calculations load data only through that verified storage boundary.
They do not accept arbitrary Parquet paths. Close-derived values become
available at `open_time + 1 hour`; the exchange `close_time` remains the final
instant inside the candle and is not treated as an observability timestamp.
Development mode rejects every request containing a 2025 month before storage
is called. Selecting final-evaluation mode requires an explicit authorization
flag even when the request does not include the holdout, and that flag is
recorded in experiment metadata.

The momentum baseline uses 24 consecutive hourly close-to-close steps, which
requires 25 closes. Positive momentum is `LONG`, nonpositive momentum is
`FLAT`, and incomplete history is `NO_SIGNAL`. `NO_SIGNAL` is not a sell.
Directional signals become readable one hour after the decision candle opens
and record an idealized next-bar-open intent. That intent is not a fill. The
hypothesis and its negative-result rules are in
`docs/research/m5-momentum-hypothesis.md`. No empirical performance has been
calculated.

Milestone 6 adds a single-threaded latency-one backtest. A directional signal
available at time T can first attempt a spot market order at the observed open
one hour later. The fill uses that open with a synthetic 10 bps fee and 5 bps
of adverse slippage. Those rates, the 10,000 USDT starting cash, and the
`0.00000001` cash and BTC quanta are modeling conventions, not a venue schedule
or lot size. There is no order-book or depth model. Bar volume is not treated
as liquidity available at the open, so a zero-volume bar can still produce a
simulated fill. Buys reserve the fee before sizing the BTC quantity. Posted
cash amounts stay on the cash-quantum grid. `NO_SIGNAL` holds an existing
position and does not invent a sale, including across missing hours. Missing
execution bars expire the order. Fresh equity is marked only at observed bar
completions; a held position during a gap keeps an explicitly stale reference
to the last completed close. A passing test shows simulation mechanics. It does
not show profitability, and it does not guarantee an exchange fill. Actual
Binance archive compatibility and licensing remain unresolved, and the 2025
holdout is not evaluated.

Risk analytics and research reporting are planned and are not yet implemented.

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

## Research protocol

Research data must enter through `ResearchDataLoader`, which calls the M3
storage workflow for each requested month and then reads Parquet timestamps with
`epoch_us`. Exact close prices remain `Decimal`. A dataset is complete only
relative to its explicitly requested months; a one-month request is never
described as the full 2021-2025 history.

Close-to-close returns use precision-50 `Decimal` arithmetic:

```text
simple return = P_t / P_(t-1) - 1
log return = ln(P_t) - ln(P_(t-1))
```

The first candle has no return. A missing hour is an explicit gap and is never
bridged by an older price. Nonpositive or nonfinite prices are rejected.

Rolling means stay in `Decimal`. Standard deviation, volatility, autocorrelation,
and empirical quantiles cross an explicit boundary to binary64. A finite
`Decimal` that overflows, underflows to zero, or has a successive nonzero gap
no larger than 1000 local binary64 roundoffs of `1e-15` makes the affected
estimate undefined. A converted gap that differs by more than a relative
tolerance of `1e-3` is also undefined. It is not replaced with zero. Pearson autocorrelation uses the two
lag-aligned samples; zero variance is undefined, and a correlation more than
`1e-10` outside `[-1, 1]` is undefined. Smaller floating-point excesses are
clamped into that interval. Quantiles use Hyndman–Fan type-7 interpolation.
The default standard deviation is the sample estimator with denominator
`n - 1`. Population deviation must be requested explicitly. Annualized
volatility multiplies that deviation by the square root of 8760, a conventional
365-day hourly count rather than the exact number of observations in every
calendar year. Incomplete windows produce no statistic. A constant series has
standard deviation zero and undefined autocorrelation.

A rolling statistic at index `i` uses only inputs through index `i` and becomes
available at the same time as that input. For a return, that time is the
current candle's `available_at`.

The approved half-open UTC splits are:

```text
train:      [2021-01-01T00:00:00Z, 2024-01-01T00:00:00Z)
validation: [2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)
holdout:    [2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)
```

Membership uses candle `open_time`. Prior candles may warm up a later split, but
they are excluded from that split's evaluation rows and from its supervised
labels. A close label is eligible only when the labeled candle is present and
its `available_at` is strictly before the split's exclusive end. The final
training candle opens at 2023-12-31 23:00 UTC and its close becomes available
exactly at 2024-01-01 00:00 UTC, so that close can inform a decision at the
validation boundary but cannot be a training label. The same rule applies at
the validation/holdout boundary. For a candle opening at 10:00 UTC,
close-derived features are unavailable at 10:00, at its internal `close_time`,
and one microsecond before 11:00; they become available at 11:00 UTC. This is a
conservative bar-completion convention, not a measurement of exchange
publication latency.

Development mode rejects any request that includes a 2025 month, or that sets
the final-evaluation authorization flag, before storage runs. Final-evaluation
mode requires that flag and records both the mode and the flag in the
experiment manifest. These checks do not stop a caller from reading a Parquet
file directly. A coverage gap records the two successive loaded opens that are
not one hour apart; it does not invent a row for every absent hour. M4 does
not fit strategies or evaluate holdout performance.

Experiment identifiers are SHA-256 hashes of canonical JSON containing protocol
version, split boundaries, feature parameters, coverage, source and Parquet
lineage, statistical conventions, and software versions. Execution time is
stored separately and does not affect the identifier. No empirical market result
is included.

Binance Vision licensing remains under review. Public redistribution of the
datasets or derived reports has not been approved. QuantForge is not
production-ready and does not support live trading.

See `docs/architecture.md` for the intended architecture and current
implementation boundary.
