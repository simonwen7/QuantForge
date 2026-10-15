# M9 readiness and protocol freeze

This document describes the offline Milestone 9 workflow. It is not a market
study and it does not accept Binance Vision terms.

## Source assumptions

The expected source is BTCUSDT spot, 1-hour candles, from January 2021 through
December 2025. That is 60 monthly archives: 36 in train, 12 in validation, and
12 in the final holdout. The ingestion code assumes a headerless 12-column
CSV, SHA-256 checksums, millisecond timestamps before 2025, and microsecond
timestamps in 2025. Close time is one tick before the next hour. Those rules
are tested with synthetic archives. They have not been checked against a real
Binance file.

## Legal uncertainty

The repository stores a URL for the Binance public-data terms and does not
contain a decision that those terms permit research use, private storage, or
publication. Download availability is not that decision. The legal gate stays
`LEGAL_UNVERIFIED`. Passing the test suite does not change it.

## What has to happen before a study

1. A person reviews the source terms and decides whether use and publication
   are allowed.
2. A separate decision authorizes acquisition of the archives.
3. The acquired months match the expected manifest, including every UTC hour,
   and the existing M2/M3 checks pass. Missing hours are not filled.
4. A person accepts the candidate protocol. The protocol id alone is not
   acceptance.
5. Train analysis, 2024 validation, and the 2025 holdout each need their own
   later authorization. Validation approval does not include the holdout.

No step in that list is active in this milestone.

## Protocol

The candidate protocol fixes BTCUSDT spot 1h, the three half-open partitions,
24-hour momentum, 24-hour relative mean reversion with a strict -2% threshold,
latency of one hour, 10,000 USDT, quanta of 0.00000001, and the three cost
rows. It does not select a grid winner. Changing a research decision changes
the protocol id. Approval state and the wall clock do not. A software defect
found later needs a new protocol id and a new human acceptance. It is not a
chance to retune the threshold.

## Coverage and the final candle

A future performance table requires strict hourly coverage of the requested
partition. The last 2025 candle opens at 2025-12-31 23:00 UTC. Its close tick
is 2025-12-31 23:59:59.999999 UTC. Research availability is 2026-01-01 00:00
UTC. That candle stays in the holdout because membership uses the open. The
close may be used as a named terminal valuation at the exclusive end. It is
not a sale, and no 2026 open is loaded to produce it. An order due at
2026-01-01 00:00 UTC does not fill. The same clock applies at the ends of
2023 and 2024, with a one-millisecond close tick.

## What the software check is

M9 tests show that the protocol hash, the scope checks, the expected
manifest, the synthetic preflight, the terminal-mark helper, and the readiness
writer behave as specified. They do not show that a rule made or lost money.
The readiness report has no return, Sharpe, or Sortino figure.

Train, validation, and the holdout are labeled not executed. The holdout line
is:

`FINAL_HOLDOUT_NOT_EXECUTED — Authorization and/or verified data prerequisites remain unresolved.`

## Reproduce the offline report

From the repository root, with the existing environment and no network:

```bash
UV_OFFLINE=1 uv run --no-sync python -c "
from pathlib import Path
from quantforge.analytics.final_report import build_readiness_report, write_readiness_report
from quantforge.analytics.verified_study import StudyPlan
from quantforge.research.access import ResearchScope
from quantforge.research.protocol_freeze import candidate_protocol_freeze
from quantforge.research.readiness import assess_evidence

report = build_readiness_report(StudyPlan(
    protocol=candidate_protocol_freeze(),
    requested_scope=ResearchScope.VERIFIED_HOLDOUT,
    authorization=None,
    preflight=assess_evidence(),
))
write_readiness_report(Path('/tmp/quantforge-m9-readiness'), report)
print(report.report_id)
"
```

Write only to a directory you choose. Do not point this at `data/raw` or
`data/processed`. The command does not download or read market archives.
