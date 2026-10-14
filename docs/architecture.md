# QuantForge Architecture

## Intended data flow

QuantForge is planned as a small set of modules connected by an explicit
research pipeline:

```text
Market Data
    -> Validation
    -> Parquet/DuckDB
    -> Research/Signals
    -> Event-Driven Backtester
    -> Risk Analytics
    -> Research Reports
```

- **Market Data:** Acquire versioned source data without altering the original
  records.
- **Validation:** Check schemas, timestamps, ordering, duplicates, gaps, and
  market-specific invariants before data is accepted.
- **Parquet/DuckDB:** Store validated data reproducibly and expose it for local
  analysis.
- **Research/Signals:** Develop deterministic features and signals while
  preventing look-ahead bias and data leakage.
- **Event-Driven Backtester:** Simulate orders, fills, fees, slippage, cash,
  and positions using information available at each event.
- **Risk Analytics:** Calculate portfolio and strategy risk from verified
  accounting outputs.
- **Research Reports:** Record assumptions, experiment configuration,
  out-of-sample results, negative findings, and limitations.

These are intended module boundaries, not separate services. Additional
infrastructure should be introduced only when a demonstrated requirement
justifies it.

## Implementation status

Implemented through Milestone 2:

- Python 3.12 package foundation using a `src` layout.
- Reproducible dependency metadata and lock file managed by `uv`.
- Import smoke test and basic lint, format, and build tooling.
- Standard-library Binance Vision adapter scoped to BTCUSDT Spot 1h monthly
  archives from 2021 through 2025.
- Bounded HTTP retries with injectable offline transport, explicit timeouts,
  streaming SHA-256 verification, and safe temporary files.
- Strict ZIP and 12-column CSV validation without archive extraction.
- Exact millisecond or microsecond conversion to timezone-aware UTC datetimes.
- Individually atomic archive and provenance publication, idempotent reuse, and
  explicit archive-correction conflict detection.
- Offline tests built entirely from generated synthetic ZIP and CSV content.

Planned, not implemented:

- Live ingestion runs, pending explicit resolution of the dataset licensing
  gate.
- Comprehensive market-data quality and economic validation.
- Storage layout and DuckDB integration.
- Signal interfaces and experiment tracking.
- Event, execution, accounting, and risk models.
- Research report generation.

## Ingestion boundary

Milestone 2 validates source integrity and structural parseability before an
archive is accepted. Its publication order is checksum retrieval, temporary
download, SHA-256 verification, ZIP validation, CSV parsing, timestamp
normalization, atomic archive publication, and atomic provenance publication.
An archive is never published at its canonical path before validation.

Archive publication and provenance publication are separate atomic operations,
not a two-file transaction. A process crash between them can leave a validated
archive without provenance. Such an orphan is never reported as successful and
is rejected on later ingestion because its original retrieval timestamp and
HTTP metadata cannot be reconstructed safely.

Accepted raw archives use deterministic paths below
`data/raw/binance-vision/spot/klines/BTCUSDT/1h/<year>/`. A JSON provenance
sidecar records only observed source and HTTP metadata. Existing archives are
re-hashed and revalidated before reuse. If a remote checksum changes, ingestion
stops with a correction conflict rather than silently replacing accepted data.

The parser treats timestamps before 2025-01-01 as milliseconds and timestamps
from 2025-01-01 as microseconds. Conversion uses integer arithmetic and
preserves exact microsecond precision. Timestamps must fall within the requested
UTC archive month, and close time must follow open time.

ZIP processing permits exactly one expected CSV member and rejects unsafe
paths, symlinks, encryption, corruption, unexpected members, and excessive
expansion. Default limits are 16 MiB compressed, 64 MiB uncompressed, a 100:1
compression ratio, and 1,000 rows. The row limit exceeds the maximum 744 normal
hourly observations in a calendar month. These limits are configurable, and
their compatibility with real Binance archives has not been verified.

The downloader uses a 10-second connection timeout, a 30-second read timeout,
and at most three attempts with bounded exponential backoff for transient
failures. Tests replace the transport and clock, generate synthetic archives,
and make no real network requests.

Provenance records source and checksum URLs, dataset identity, expected and
actual SHA-256 values, archive size, UTC retrieval time, source timestamp unit,
schema/parser versions, Dataset Terms reference, member name, and only observed
HTTP metadata. Serialization is deterministic for a fixed injected clock and
inputs.

This layer deliberately does not perform gap analysis, duplicate detection,
OHLC relationship checks, outlier analysis, or analytical storage. Those
responsibilities remain in the validation and Parquet/DuckDB stages.

## Initial research scope

The initial planned market is BTCUSDT Spot at 1-hour OHLCV resolution. This
scope does not imply that data has been approved, acquired, or validated.
Timestamp semantics, trading-calendar expectations, missing intervals,
duplicates, price and volume constraints, and source-file integrity must be
defined and tested before research begins.

Binance Vision is a tentative source only. Its licensing terms and suitability
for this project remain unresolved gates. Dataset provenance, completeness,
schema stability, and historical correctness are also unresolved validation
gates. No Binance Vision data should be treated as approved research input
until both gates are explicitly resolved.

No real-data integration has been verified. Public redistribution of Binance
Vision datasets or derived reports has not been approved. The platform is not
production-ready.
