# M8 research hypothesis: hourly relative mean reversion

This document pre-specifies a second research rule and a fixed-rule
chronological evaluation. It does not report an empirical result, and it is
not evidence that the rule is profitable.

## Hypothesis

On BTCUSDT spot, a close that is at least 2% below the mean of the previous
24 hourly closes may be followed by a partial reversal. The position is long
only while that condition is true, and flat otherwise. The later comparison,
when a real-data study is separately authorized, is executable equity after
the existing synthetic fee and slippage, against cash and buy-and-hold on the
same clock.

A persistent decline can keep the close below its trailing mean. The rule
then stays long. That failure is part of the hypothesis. The baseline is not
adjusted to avoid it.

## Formula

`L = 24`. The window is 25 consecutive hourly closes. The mean uses the
previous 24 closes and excludes the decision close.

```text
prior_mean_t = (P_(t-L) + ... + P_(t-1)) / L
deviation_t = P_t / prior_mean_t - 1
```

`P` is the completed close. Arithmetic uses Decimal precision 50. The
threshold `0.02` is a pre-specified convention, not an estimated quantile.

- `deviation_t < -0.02`: `LONG`
- `deviation_t >= -0.02`: `FLAT`, including exact equality
- fewer than 24 preceding observations: `NO_SIGNAL` / `WARMUP`
- a full window that is not an exact hourly chain: `NO_SIGNAL` / `GAP`

For a prior mean of exactly 100, a close of 97.99 is long, and closes of 98
and 98.01 are flat. `NO_SIGNAL` has no execution intent. `LONG` and `FLAT`
name `NEXT_BAR_OPEN`. Availability is one hour after the decision open.
There is no hysteresis and no internal position state.

The feature is not the 24-hour endpoint momentum ratio. A negative endpoint
return can still leave the close above the trailing mean, and a close below
the mean can occur without matching that endpoint sign. The configuration
hash is separate from the M5 momentum hash.

## Fixed-rule chronological evaluation

The evaluation is a fixed-rule chronological walk-forward. It does not fit
parameters, and it is not an optimized walk-forward selection.

| Fold | History available at the cutoff | Evaluation |
|---|---|---|
| 1 | [2021-01-01, 2022-01-01) | [2022-01-01, 2022-07-01) |
| 2 | [2021-01-01, 2022-07-01) | [2022-07-01, 2023-01-01) |
| 3 | [2021-01-01, 2023-01-01) | [2023-01-01, 2023-07-01) |
| 4 | [2021-01-01, 2023-07-01) | [2023-07-01, 2024-01-01) |

All four evaluation windows are inside the training partition and do not
overlap. History expands from 2021-01-01. The 2024 validation year and the
2025 holdout are not inputs.

Each fold starts with 10,000 USDT and zero BTC. Earlier closes are feature
context only. A decision that opened before the fold does not open a position
inside it. The first in-fold decision is available at `fold_start + 1 hour`
and its earliest fill is `fold_start + 2 hours`. The pre-trade anchor is the
mark at `fold_start + 1 hour`. An order due at exactly `fold_end` does not
fill in that fold, and the next fold does not inherit it.

The four equity curves are independent. They are not stitched, and their
total returns are not compounded into one three-year result.

Momentum, mean reversion, cash, and buy-and-hold share each fold's bars,
valuation timestamps, latency, costs, and anchor. Buy-and-hold enters at the
first in-fold directional decision even when that decision is flat. Cash
never buys. Costs are the existing zero, baseline, and doubled rows. The
baseline study therefore has 4 x 3 x 4 = 48 outcomes.

## Sensitivity is not selection

The sensitivity grid is frozen:

- lookback in {12, 24, 48}
- threshold in {0.01, 0.02, 0.05}
- the same three cost rows

On the four production folds that is 108 mean-reversion outcomes. Every cell
is reported, including zero fills, negative total returns, and undefined
Sharpe or Sortino. Nothing is ranked or dropped. The cell at lookback 24 and
threshold 0.02 is the same baseline rule run again. It is not independent
confirmation. Lookbacks of 12, 24, and 48 hours share each fold's evaluation
window, anchor, and entry. The extra hours are feature context. They do not
move the evaluation start and they do not shorten another cell's window.
Synthetic regime fixtures (rising, falling, oscillating, constant, and a
missing hour) test software paths only.

A future authorized training-only selector would need a frozen grid, an
unchanged first fold, at least three prior folds with defined total returns,
no Sharpe ranking, no use of undefined metrics as zeros, and no refit after
2024 is seen. That selector is not implemented. M8 does not run it on
synthetic data either, because a synthetic winner would not be evidence.

## Identity and gaps

A fold id binds the history and evaluation boundaries, the bar contents and
source hashes, every policy run id, the cost row, the valuation convention,
and the hourly as-of return convention. A study id binds those fold ids, the
sensitivity cell ids, and the methodology version. Wall-clock time is not an
input. Two different price paths do not share an id merely because their
dates match.

A missing hour stays missing. Sharpe, Sortino, and volatility follow the M7
rule and are undefined when the hourly grid is incomplete. Total return and
drawdown remain labeled observed-path figures. A production fold with missing
hours is rejected rather than shortened into an apparently complete
semiannual study. A shorter injected calendar is labeled
`injected_synthetic` and is not that study.

## What this milestone does not claim

No real BTCUSDT performance has been measured. Passing tests show that the
signal, the fold boundaries, and the report identity behave as specified.
They do not show a reversal edge, and they do not authorize 2024 validation
or the 2025 holdout. Binance archive compatibility and licensing remain
unresolved.
