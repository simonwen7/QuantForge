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

Implemented through Milestone 5:

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
- Verified research loading through the M3 storage boundary, with explicit
  requested-month coverage and preserved validation warnings.
- Decimal simple and log returns, trailing complete-window statistics, and
  explicit candle availability at one hour after `open_time`.
- Fixed chronological train, validation, and final-holdout splits, including
  warm-up separation, future-label embargo, and development-mode holdout denial.
- Canonical experiment manifests and SHA-256 identifiers bound to source lineage.
- A fixed long-or-flat 24-hour time-series momentum signal with Decimal
  momentum, causal availability, and per-close source lineage.
- A latency-one event-driven spot backtest with Decimal cash and BTC
  accounting, explicit fees and adverse slippage, and a separate run identifier.

Planned, not implemented:

- Live ingestion runs, pending explicit resolution of the dataset licensing
  gate.
- Real-source compatibility testing and broader economic anomaly analysis.
- Performance statistics, risk analytics, and research report generation.

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

## Research boundary

M4 consumes verified M3 partitions. The loader calls the M3 publication
workflow, which reparses and revalidates each source archive. A caller-supplied
Parquet path is not an input and is not treated as verified data. The loader
then reads `epoch_us` timestamps, preserves `Decimal` closes, and rejects
duplicate, unordered, or unexpected coverage. Strict mode requires the requested
months to be present and calendar-contiguous. Explicit partial mode must be
selected in configuration and cannot be reported as complete coverage. A
coverage gap stores the previous and next loaded open times when they are not
exactly one hour apart. It detects the discontinuity without synthesizing one
row per missing hour.

`available_at = open_time + 1 hour` for every one-hour candle. The stored
`close_time` remains either one millisecond or one microsecond before that
boundary, depending on the source year. Prices, returns, and any object
exposing the same availability timestamp are rejected until `available_at`.
Rolling statistics are index-aligned with their inputs: index `i` uses only
earlier and current inputs and is available when input `i` is available.

Returns use a local `Decimal` precision of 50:

```text
simple return = P_t / P_(t-1) - 1
log return = ln(P_t) - ln(P_(t-1))
```

The first row has no return. A non-hourly step is an explicit gap and is never
filled from an older price. Standard deviation, volatility, autocorrelation,
and Hyndman–Fan type-7 quantiles convert finite `Decimal` values to binary64.
The conversion rejects non-finite results and underflow to zero. A successive
nonzero gap is undefined when it is at most 1000 local roundoffs of `1e-15`,
or when binary64 changes it by more than a relative tolerance of `1e-3`. That
condition makes a rolling estimate undefined and makes a one-sample summary
fail.
Autocorrelation is the Pearson correlation of the lag-aligned samples. Zero
variance is undefined. Correlations more than `1e-10` outside `[-1, 1]` are
undefined; smaller excesses are clamped. Sample deviation divides by `n - 1`
unless population deviation is requested. Volatility scales that deviation by
`sqrt(8760)`. The factor 8760 is a 365-day convention, not a claim about every
calendar year. Trailing windows are complete or absent. Constant deviation is
zero; constant autocorrelation is undefined.

The protocol boundaries are fixed:

```text
train:      [2021-01-01T00:00:00Z, 2024-01-01T00:00:00Z)
validation: [2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)
holdout:    [2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)
```

Split membership uses `open_time`. A close label also requires the labeled
candle to be present and its `available_at` to precede the exclusive split end.
Equality with that end is outside the split, so the last candle in each split
cannot supply a same-split label at horizon zero, and a one-hour label whose
future close becomes available exactly at the boundary is excluded too. Warm-up
rows remain outside evaluation and outside the later split's label set. M4 has
no parameter-fitting operation, so validation and holdout rows are not consumed
as training labels.

Development access fails before storage when any requested or supplied month is
in 2025, and also when the final-evaluation flag is set. Final evaluation
requires that flag. Both the mode and the flag are hashed into experiment
identity. These controls reduce accidental holdout use through the loader and
manifest builder. They do not prevent direct filesystem reads of a published
2025 partition, and they are not a substitute for procedural control of the
final evaluation.

Experiment identity hashes canonical configuration, actual and expected
coverage, source checksum lineage, return and statistic conventions, label
availability, and software versions. `allow_nan` is disabled. The creation
timestamp is stored separately and excluded from the hash. Identical inputs
therefore produce identical identifiers. M4 does not estimate strategy
performance or test hypotheses that assume independent returns. Real Binance
compatibility and licensing remain unresolved, and no result here is evidence
of trading profitability.

## Strategy boundary

M5 turns verified closes into causal signals. The baseline momentum is

```text
momentum_t = P_t / P_(t-24) - 1
```

calculated once at `Decimal` precision 50 after checking that the 25 closes
form 24 exact hourly steps. The strategy does not sort or fill inputs. Each
signal lists every inspected close and its source archive identity, so a change
in an earlier month remains visible when the decision candle is unchanged.
Warmup and gap signals record only observations that exist. Warmup takes
precedence while fewer than 24 preceding observations exist, including when
that short prefix is discontinuous. A gap applies only to a full trailing
window.

Directional signals are readable at `available_at` and name `NEXT_BAR_OPEN` as
the earliest eligible bar open. They do not contain a quantity, fee, or fill.
`NO_SIGNAL` has no execution intent. M5 does not modify M4 experiment
identifiers. Its own configuration hash is separate. Dataset signaling uses the
M4 loader and refuses development-mode 2025 data and final-evaluation mode.
The documented hypothesis is not an observed performance result.

## Backtest boundary

M6 replays hourly bars in one thread. At a shared timestamp the order is: bar
open, due fill, prior-bar completion, signal release, order submission, then
valuation. Completion of a bar is scheduled from that bar and still occurs when
the next open is missing. The default delay is one hour after `available_at`.
Zero-latency fills are not implemented. A missing due bar expires the order; the
order is not moved to a later open.

The execution open is loaded by `MarketBarLoader` through the same M3
publication path as research closes. A bar-open event exposes the open only.
High, low, close, and volume are copied into a completion view at
`available_at`. `PriceObservation` stays close-only, and M4 experiment
identifiers are unchanged. Caller-built bars are labeled synthetic and are not
verified market data.

The default book starts at 10,000 USDT, charges a 10 bps fee, and applies 5 bps
of adverse slippage. Those rates, and the `0.00000001` cash and BTC quanta, are
simulation assumptions. They are not a Binance fee schedule or lot size. The
engine does not model an order book or market depth, does not support leverage,
borrowing, shorting, or derivatives, and does not use bar volume to predict
liquidity at the open. A zero-volume bar can still fill. Buy quantity is
rounded down after reserving the fee. The posted debit is rounded up to the
cash quantum and the posted sell credit is rounded down. Initial cash must
already lie on that quantum, and every posted cash movement does too, so the
cash balance cannot fall between quanta. `rounding_residual` records the
difference between the exact product and that posted cash amount. It is smaller
than one quantum and is not itself a cash balance. Uninvested cash after an
all-in buy remains on the grid and can exceed one quantum, because one BTC
quantum can cost more than one cash quantum. The recorded fee is the posted
fee. A sell that cannot pay a positive cash credit is rejected.

`LONG` buys only from a flat book. `FLAT` sells the entire BTC position.
`NO_SIGNAL` cancels an order that is still pending and does not reverse a fill
that already occurred at that timestamp. It can leave BTC held through missing
hours. Fresh equity uses the open at the valuation timestamp when that open was
observed, because that print is available and matches a fill just booked there.
The completed close is used only when the timestamp has no open. The mark records
whether the price was an open or a close. A stale record repeats the last fresh
price, keeps that price's timestamp, and is not an equity point, so the curve is
not filled across the gap. The run identifier hashes the execution assumptions,
the valuation convention, the signal sequence,
and, for synthetic bars, the full OHLCV identity. It does not include wall-clock
time and does not alter M4 or M5 identifiers. Development mode still rejects
2025 before storage, and M6 refuses final-holdout evaluation. No performance
statistic is calculated. A passing backtest is not a market-fill guarantee or
evidence of profitability. Real Binance licensing and archive compatibility
remain unresolved.

## Analytics boundary

M7 reads M6 equity points, fills, and run identifiers. It does not replace the
ledger. A simple return is `E_t / E_(t-1) - 1` at Decimal precision 50, and only
when the two fresh in-evaluation marks are exactly one hour apart. Those marks
follow the as-of rule, so the series is not a close-to-close price series.
A sample that contains both an open and a completed close says so. The sample
starts at the pre-trade mark one hour before the shared post-warmup entry, so
the entry cost is inside the first return. Earlier warm-up marks stay out of
the metric sample. A mark before a split boundary is not borrowed as a
validation anchor.

Total return is the endpoint ratio. Maximum drawdown is the most negative
observed `equity / running peak - 1`. Both remain defined when hours are missing
and are labeled as observed-point results. Sharpe, Sortino, and annualized
volatility are undefined on an incomplete hourly grid. They are not computed on
the longest segment. Sharpe is `mean(r) / sample_std(r) * sqrt(8760)` with a
zero hourly risk-free rate and sample denominator `n - 1`. Sortino uses the same
numerator and `sqrt(8760)`, but the denominator is `sqrt(sum(min(r, 0)^2) / n)`
over every hourly return. A zero denominator is an explicit undefined reason.
Dispersion estimates reuse the M4 binary64 guard. `8760` is the same 365-day
convention as M4.

Cash submits no orders. Buy-and-hold buys at the first execution time the fixed
24-hour rule can be directional, then holds. Its positive momentum field is an
order-contract placeholder, not a measured signal, and the report does not treat
it as one. A strategy `FLAT` at that decision does not delay the benchmark. The
three cost rows are zero, the M6 baseline, and double those rates. All nine
runs are retained. The report id hashes the policy version, run ids, scenarios,
evaluation clock, and valuation convention. It does not hash the wall clock.
`python -m quantforge.analytics` writes the synthetic demonstration. It does not
read verified storage or 2025 data, and it does not claim profitability.

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
