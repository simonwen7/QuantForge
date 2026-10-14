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

Implemented through Milestone 3:

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
- Deterministic monthly and cross-month quality validation that preserves
  source values and ordering.
- Stable blocking-error and nonblocking-warning issue codes for calendar,
  timestamp, OHLC, volume, trade-count, Decimal, and source-lineage checks.
- Canonical monthly Parquet partitions written and verified with in-memory
  DuckDB, using exact `DECIMAL(38,18)` and UTC `TIMESTAMPTZ` columns.
- Processed manifests and validation reports with source-checksum lineage,
  deterministic coverage, integrity hashes, and explicit processing versions.
- Idempotent processed-partition reuse and explicit source or schema conflicts.

Planned, not implemented:

- Live ingestion runs, pending explicit resolution of the dataset licensing
  gate.
- Real-source compatibility testing and broader economic anomaly analysis.
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

M2 deliberately does not perform gap analysis, duplicate detection, OHLC
relationship checks, outlier analysis, or analytical storage. M3 provides the
deterministic structural and financial checks required before analytical
storage; source ingestion remains unchanged.

## Data quality boundary

M3 assesses source observations in their original order. It never sorts,
interpolates, forward-fills, removes, or corrects candles. Blocking errors
include an incorrect calendar row count, missing or duplicate hours,
out-of-order input, non-hourly or out-of-month timestamps, incorrect close-time
conventions, nonpositive or inconsistent OHLC prices, invalid volume or trade
values, values that cannot fit `DECIMAL(38,18)`, and provenance identity
mismatches. Zero volume, zero trades, and a nonzero Binance `ignore` field are
warnings: they do not block publication, but remain visible in
`validation.json`.

A complete UTC month contains 672, 696, 720, or 744 hourly observations,
depending on calendar length and leap year. Pre-2025 close times are exactly
one hour less one millisecond after their open; 2025 close times use one
microsecond. Cross-month assessment reports missing months and discontinuities
without implying that a valid subset covers the full 2021-2025 range.

No arbitrary price-outlier threshold is applied. Broader statistical anomaly
analysis and any policy for source corrections remain future work.

## Processed storage boundary

Parquet is the canonical processed representation. DuckDB is used in memory to
write, read, verify, and query it; M3 does not create a persistent DuckDB
database. Financial columns use `DECIMAL(38,18)`, counts use `BIGINT`, and
timestamps use `TIMESTAMPTZ` with every DuckDB connection explicitly configured
for UTC. Values that require rounding or exceed supported precision are
rejected before insertion. A declared scale greater than 18 is rejected even
when excess places are zeros, so storage never silently coerces source scale.
Publication requires exact Decimal and microsecond timestamp equality after
readback. Direct single-file queries set
`hive_partitioning = false` so path-derived partition fields cannot silently
alter the verified file schema. Readback compares integer epoch-microsecond
values, avoiding an undeclared `pytz` dependency in DuckDB's Python timestamp
conversion. DuckDB reports overflow for aggregates whose result exceeds
`DECIMAL(38,18)`; M3 tests both exact representable sums and this explicit
failure rather than permitting silent rounding.

Processed months use deterministic paths:

```text
data/processed/binance-vision/spot/klines/
  symbol=BTCUSDT/
    interval=1h/
      year=2024/
        month=01/
          BTCUSDT-1h-2024-01.parquet
          manifest.json
          validation.json
```

The secure storage API accepts an M2 ingestion result only to locate the raw
archive and sidecar. It does not accept caller-created observations or a
caller-provided validation result. It reads strict provenance from disk,
compares it with the supplied M2 result, rehashes and reparses the archive,
reruns current quality rules, writes all output into a temporary sibling
directory, reads back every Parquet row, and verifies the schema, values,
timestamps, identity, and lineage before publication.

The manifest records raw archive and provenance SHA-256 values, source and
parser versions, request identity, current validation and storage versions,
DuckDB version, Parquet SHA-256 and byte size, observation coverage, warning
count, and UTC creation time. `validation.json` stores all current issues and
is itself hashed by the manifest. An unchanged source and compatible partition
is fully reverified before reuse. Changed source hashes, provenance, or
processing versions raise explicit conflicts. Corrupt or incomplete existing
partitions are rejected and never overwritten.

All three partition files are assembled and synchronized before a
same-filesystem directory rename. Failures before rename remove temporary
output. The source archive and provenance are rehashed after parsing and again
immediately before publication. After rename, the canonical directory is
reopened and fully verified before success is returned. Concurrent cooperating
writers rely on the operating system refusing to rename over the winner's
nonempty partition; the loser verifies and reuses that output. This behavior is
covered on the supported macOS test environment. Unexpected partition entries
are rejected.

A crash after rename but before parent-directory synchronization can leave a
complete partition that is verified on retry; no claim of perfect crash
durability is made for every operating system or filesystem. Persistent source
mutation at the checked boundaries is detected, but this is not protection
against malicious filesystem modification or a file changed and restored
between checks.

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
