"""Synthetic bars and signals for backtest tests. Not verified market data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.research import synthetic_market_bar
from quantforge.research.market_bars import MarketBar
from quantforge.strategies import (
    MomentumConfig,
    strategy_config_id,
)
from quantforge.strategies.models import (
    LookbackSource,
    NoSignalReason,
    SignalAction,
    StrategySignal,
)

HOUR = timedelta(hours=1)
CONFIG_ID = strategy_config_id(MomentumConfig(lookback_hours=1))


def bars_at(
    start: datetime,
    opens: tuple[str, ...],
    closes: tuple[str, ...],
    *,
    archive_sha: str = "a" * 64,
    highs: tuple[str, ...] | None = None,
) -> tuple[MarketBar, ...]:
    """Build hourly synthetic bars. Completion fields default away from the open."""
    if start.tzinfo is not UTC:
        raise ValueError("start must be UTC")
    rows = []
    for offset, (raw_open, raw_close) in enumerate(zip(opens, closes, strict=True)):
        open_decimal = Decimal(raw_open)
        close_decimal = Decimal(raw_close)
        high_decimal = (
            max(open_decimal, close_decimal)
            if highs is None
            else Decimal(highs[offset])
        )
        rows.append(
            synthetic_market_bar(
                start + offset * HOUR,
                raw_open,
                close=raw_close,
                high=high_decimal,
                low=min(open_decimal, close_decimal, high_decimal),
                archive_sha=archive_sha,
            )
        )
    return tuple(rows)


def _sources(
    end: datetime, lookback: int, archive_sha: str
) -> tuple[LookbackSource, ...]:
    start = end - timedelta(hours=lookback)
    return tuple(
        LookbackSource(
            open_time=start + timedelta(hours=offset),
            source_year=(start + timedelta(hours=offset)).year,
            source_month=(start + timedelta(hours=offset)).month,
            source_archive_sha256=archive_sha,
            source_schema_version="synthetic-unverified",
        )
        for offset in range(lookback + 1)
    )


def directional_signal(
    open_time: datetime,
    action: SignalAction,
    momentum: str,
    *,
    lookback: int = 1,
    archive_sha: str = "a" * 64,
    config_id: str = CONFIG_ID,
) -> StrategySignal:
    """Build one valid directional signal aligned to ``open_time``."""
    tick = (
        timedelta(milliseconds=1)
        if open_time.year < 2025
        else timedelta(microseconds=1)
    )
    return StrategySignal(
        open_time=open_time,
        close_time=open_time + HOUR - tick,
        available_at=open_time + HOUR,
        action=action,
        momentum=Decimal(momentum),
        no_signal_reason=None,
        strategy_config_id=config_id,
        lookback_hours=lookback,
        lookback_start=open_time - timedelta(hours=lookback),
        lookback_end=open_time,
        sources=_sources(open_time, lookback, archive_sha),
        execution_policy="NEXT_BAR_OPEN",
        earliest_eligible_open=open_time + HOUR,
    )


def warmup_signal(
    open_time: datetime,
    *,
    lookback: int = 1,
    config_id: str = CONFIG_ID,
) -> StrategySignal:
    """Build a warm-up abstention with no execution intent."""
    tick = (
        timedelta(milliseconds=1)
        if open_time.year < 2025
        else timedelta(microseconds=1)
    )
    return StrategySignal(
        open_time=open_time,
        close_time=open_time + HOUR - tick,
        available_at=open_time + HOUR,
        action=SignalAction.NO_SIGNAL,
        momentum=None,
        no_signal_reason=NoSignalReason.WARMUP,
        strategy_config_id=config_id,
        lookback_hours=lookback,
        lookback_start=open_time,
        lookback_end=open_time,
        sources=_sources(open_time, 0, "a" * 64),
        execution_policy=None,
        earliest_eligible_open=None,
    )
