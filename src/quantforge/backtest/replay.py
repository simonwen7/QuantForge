"""Validate and index synthetic or verified bars before replay."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC

from quantforge.backtest.models import BacktestDataError
from quantforge.research.market_bars import MarketBar
from quantforge.strategies.mean_reversion import MeanReversionSignal
from quantforge.strategies.models import StrategySignal

ReplaySignal = StrategySignal | MeanReversionSignal


def prepare_bars(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    """Reject duplicate or reversed opens before any order is created."""
    previous = None
    prepared: list[MarketBar] = []
    for bar in bars:
        if not isinstance(bar, MarketBar):
            raise BacktestDataError("replay input must be MarketBar values")
        if bar.open_time.tzinfo is not UTC:
            raise BacktestDataError("bar open_time must use datetime.UTC")
        if previous is not None and bar.open_time <= previous:
            raise BacktestDataError(
                "duplicate or reversed bar timestamps cannot produce a partial replay"
            )
        prepared.append(bar)
        previous = bar.open_time
    return tuple(prepared)


def prepare_signals(
    signals: Sequence[ReplaySignal],
    bars: tuple[MarketBar, ...],
) -> dict[object, ReplaySignal]:
    """Index signals by availability time and require a matching decision bar."""
    opens = {bar.open_time: bar for bar in bars}
    indexed: dict[object, ReplaySignal] = {}
    previous = None
    for signal in signals:
        if not isinstance(signal, (StrategySignal, MeanReversionSignal)):
            raise BacktestDataError(
                "replay signals must be StrategySignal or MeanReversionSignal values"
            )
        if signal.available_at in indexed:
            raise BacktestDataError("duplicate signal availability timestamps")
        if previous is not None and signal.available_at <= previous:
            raise BacktestDataError("signals must be strictly chronological")
        bar = opens.get(signal.open_time)
        if bar is None:
            raise BacktestDataError("signal decision bar is not in the replay")
        if (
            bar.available_at != signal.available_at
            or bar.close_time != signal.close_time
        ):
            raise BacktestDataError(
                "signal availability does not match its decision bar"
            )
        decision = signal.sources[-1]
        if (
            decision.open_time != bar.open_time
            or decision.source_archive_sha256 != bar.source_archive_sha256
            or decision.source_year != bar.source_year
            or decision.source_month != bar.source_month
            or decision.source_schema_version != bar.source_schema_version
        ):
            raise BacktestDataError(
                "signal decision source does not match the market bar"
            )
        indexed[signal.available_at] = signal
        previous = signal.available_at
    return indexed
