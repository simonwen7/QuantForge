# M5 research hypothesis: hourly BTCUSDT time-series momentum

This document pre-specifies a research hypothesis. It does not report an
empirical result, and it is not evidence that the rule is profitable.

## Motivation

Short-horizon time-series momentum asks whether a positive recent return tends
to be followed by another positive return. For this baseline the question is
narrower: on BTCUSDT spot, does the sign of the preceding 24-hour close-to-close
return have continuation into the next completed hour? The rule is long or
flat. It does not test downside continuation, leverage, or short sales.

## Scope and formula

The market is BTCUSDT spot, one-hour candles, using verified research closes.
The pre-specified baseline is `lookback_hours = 24`. Twenty-four hours is one
conventional spot trading day at this bar size. It was fixed before any
performance measurement and is not an optimized parameter. The window is 24
consecutive one-hour steps and therefore 25 closes, not a 23-hour move across
24 closes.

For decision candle `t`, when every step from `t-24` through `t` is exactly one
hour:

```text
momentum_t = P_t / P_(t-24) - 1
```

`P` is the candle close. The ratio uses local `Decimal` precision 50. It is not
a chain of rounded one-hour returns. For positive prices its sign matches the
24-hour log return; the stored value is the simple return.

Classification:

- `momentum_t > 0`: `LONG`
- `momentum_t <= 0`: `FLAT`
- fewer than 24 preceding observations: `NO_SIGNAL` / `WARMUP`
- a full 25-close window with any missing hour: `NO_SIGNAL` / `GAP`

`WARMUP` takes precedence over `GAP`. If the decision candle has fewer than 24
preceding observations, the reason is `WARMUP` even when that short prefix is
discontinuous. `GAP` is used only after 25 observations are available and the
trailing 25 are not exact hourly steps. A gap older than the current 25-close
window does not keep later complete windows from signaling. The first valid
signal after a gap is the first candle whose own trailing 25 closes are
consecutive hours.

`NO_SIGNAL` is not `LONG`, `FLAT`, hold, buy, or sell. It is not a flat
prediction and not an instruction to trade. Duplicate or reversed timestamps
abort generation rather than being sorted or repaired.
Missing hours are not interpolated.

The API accepts other positive lookbacks so the mechanism can be tested. M5
does not search them. A later sensitivity set, if separately authorized, is
limited to {12, 24, 48, 168} hours, inspected on training data and confirmed
once on 2024 validation.

## Causal timing

A signal is aligned to candle `t` and becomes readable at
`available_at = open_time + 1 hour`. It is unavailable at `open_time`, at the
exchange `close_time`, and one microsecond before `available_at`. The 2024
close is one millisecond before that hour. The 2025 close is one microsecond
before it. This is a bar-completion convention, not a measurement of exchange
publication latency.

The signal uses only closes at or before `t`. Changing a later close must not
change it. Its lineage lists every inspected close, in order, with source year,
month, archive SHA-256, and schema version. A warmup or gap records only the
closes actually present.

`LONG` and `FLAT` carry an idealized execution intent named `NEXT_BAR_OPEN`.
The earliest eligible bar open is `available_at`. That is not a guaranteed fill,
not permission to use the next bar's open price, and not an order quantity.
M6 must decide latency, missing bars, fees, and slippage. `NO_SIGNAL` carries
no execution intent.

A signal whose decision candle opens at 2023-12-31 23:00 UTC becomes available
at 2024-01-01 00:00 UTC. Membership follows the decision candle's `open_time`,
so the signal belongs to training. It is not a training label, because that
close is not observable strictly before the training split ends. M5 does not
calculate the later return.

## Later descriptive target

The pre-specified descriptive target, for a later milestone, is the next
completed hourly close-to-close return after a valid `LONG` or `FLAT` signal.
That return is a statistical object. It is not executable profit and loss.
Executable results depend on M6's fill, fee, and slippage model, which M5 does
not implement.

Planned comparators, also deferred:

- Cash: remain flat, with no position change.
- Buy and hold: one long entry at the first eligible validation bar open and no
  hourly flipping through the validation split.

M6 must require fee and slippage assumptions explicitly. No number in this
document is Binance's fee schedule. A later evaluation should show both a
zero-cost case and at least one pessimistic cost case, each labeled as an
assumption.

## What would count as a negative result

Later evaluation can reject the hypothesis if any of the following occurs:

- On 2024 validation, performance after the pre-specified pessimistic cost case
  does not beat cash.
- On training data, the average next hourly close-to-close return after `LONG`
  is not positive.
- The sign of the validation comparison against cash flips across the
  pre-specified lookbacks {12, 24, 48, 168}.

None of these comparisons has been run. A passing software test is not support
for the hypothesis.

## Data and holdout

Verified months enter through `ResearchDataLoader`. Development mode rejects a
2025 source or expected month before storage runs. M5 dataset signaling also
refuses development data that contains 2025 observations and refuses
final-evaluation mode. The final-holdout pathway remains future work for the
later final-evaluation milestone and is not authorized in M5.
Directly constructed synthetic 2025 timestamps may be used to test the
microsecond convention. They are not the holdout dataset. Reading a Parquet
file directly remains outside the application guard.

Real Binance compatibility and licensing are unresolved.

## Expected weaknesses

Hourly crypto returns are noisy. A 24-hour window is a convention, not an
estimated optimum. Fees can erase frequent flips. BTC's upward drift can make
buy and hold difficult to beat. The long-only rule ignores negative
continuation. A data gap has no position policy yet. The next-open intent
assumes zero decision and order latency. The research model does not currently
expose the next candle's open, so M5 cannot price that intent.
