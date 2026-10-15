"""Cash and buy-and-hold paths executed by the M6 engine.

Buy-and-hold enters at the first fill time the supplied rule can be
directional. Its ``LONG`` momentum value is an adapter required by the momentum
signal contract. It is not a measured feature and is not a report field.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from quantforge.analytics.models import (
    BENCHMARK_ADAPTER_MOMENTUM,
    BUY_AND_HOLD_STRATEGY_ID,
    CASH_STRATEGY_ID,
    AnalyticsError,
)
from quantforge.backtest.models import due_time_for
from quantforge.research.market_bars import MarketBar
from quantforge.strategies.mean_reversion import MeanReversionSignal
from quantforge.strategies.models import (
    NEXT_BAR_OPEN,
    LookbackSource,
    NoSignalReason,
    SignalAction,
    StrategySignal,
)

DirectionalSignal = StrategySignal | MeanReversionSignal

_HOUR = timedelta(hours=1)


def first_directional(signals: Sequence[DirectionalSignal]) -> DirectionalSignal:
    """Return the earliest LONG or FLAT decision in one validated signal stream."""
    for signal in signals:
        if not isinstance(signal, (StrategySignal, MeanReversionSignal)):
            raise AnalyticsError("unsupported signal type")
        if signal.action is not SignalAction.NO_SIGNAL:
            return signal
    raise AnalyticsError("the fixed warm-up produced no directional decision")


def entry_fill_time(signals: Sequence[DirectionalSignal]) -> datetime:
    """Return the latency-one open of the first directional decision."""
    return due_time_for(first_directional(signals).available_at)


def cash_signals(bars: Sequence[MarketBar]) -> tuple[StrategySignal, ...]:
    """Build a no-order signal for every bar. Cash never buys BTC."""
    return tuple(
        _abstain(bar, CASH_STRATEGY_ID, NoSignalReason.WARMUP, (bar,)) for bar in bars
    )


def buy_and_hold_signals(
    bars: Sequence[MarketBar],
    strategy_signals: Sequence[DirectionalSignal],
) -> tuple[StrategySignal, ...]:
    """Buy once at the post-warmup open, then hold through later ``LONG`` no-ops.

    A strategy ``FLAT`` at the first directional decision does not move this
    entry later. Bars before that decision abstain. A later break emits
    ``NO_SIGNAL`` so an existing position is held rather than sold.
    """
    entry_open = first_directional(strategy_signals).open_time
    by_time = {bar.open_time: bar for bar in bars}
    if entry_open not in by_time:
        raise AnalyticsError("buy-and-hold entry is not one of the market bars")
    signals: list[StrategySignal] = []
    for bar in bars:
        if bar.open_time < entry_open:
            signals.append(
                _abstain(bar, BUY_AND_HOLD_STRATEGY_ID, NoSignalReason.WARMUP, (bar,))
            )
            continue
        previous = by_time.get(bar.open_time - _HOUR)
        if previous is None:
            signals.append(
                _abstain(bar, BUY_AND_HOLD_STRATEGY_ID, NoSignalReason.GAP, (bar,))
            )
            continue
        signals.append(_hold_long(previous, bar))
    if not any(signal.action is SignalAction.LONG for signal in signals):
        raise AnalyticsError("buy-and-hold could not form a causal entry signal")
    return tuple(signals)


def _hold_long(previous: MarketBar, bar: MarketBar) -> StrategySignal:
    return StrategySignal(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        action=SignalAction.LONG,
        momentum=BENCHMARK_ADAPTER_MOMENTUM,
        no_signal_reason=None,
        strategy_config_id=BUY_AND_HOLD_STRATEGY_ID,
        lookback_hours=1,
        lookback_start=previous.open_time,
        lookback_end=bar.open_time,
        sources=(_source(previous), _source(bar)),
        execution_policy=NEXT_BAR_OPEN,
        earliest_eligible_open=bar.available_at,
    )


def _abstain(
    bar: MarketBar,
    config_id: str,
    reason: NoSignalReason,
    inspected: tuple[MarketBar, ...],
) -> StrategySignal:
    return StrategySignal(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        action=SignalAction.NO_SIGNAL,
        momentum=None,
        no_signal_reason=reason,
        strategy_config_id=config_id,
        lookback_hours=1,
        lookback_start=inspected[0].open_time,
        lookback_end=bar.open_time,
        sources=tuple(_source(item) for item in inspected),
        execution_policy=None,
        earliest_eligible_open=None,
    )


def _source(bar: MarketBar) -> LookbackSource:
    return LookbackSource(
        open_time=bar.open_time,
        source_year=bar.source_year,
        source_month=bar.source_month,
        source_archive_sha256=bar.source_archive_sha256,
        source_schema_version=bar.source_schema_version,
    )
