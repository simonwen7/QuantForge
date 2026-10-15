"""Momentum signals, boundaries, gaps, and deterministic run identity."""

from datetime import UTC, datetime
from decimal import Decimal

from quantforge.backtest import (
    BacktestEngine,
    EvaluationWindow,
    ExecutionAssumptions,
)
from quantforge.backtest.models import (
    BacktestConfigurationError,
    BacktestDataError,
    PriceKind,
)
from quantforge.research import (
    HOLDOUT_START,
    TRAIN_START,
    VALIDATION_START,
    PriceObservation,
    synthetic_market_bar,
)
from quantforge.research.market_bars import MarketBar
from quantforge.strategies import MomentumConfig, MomentumStrategy, SignalAction
from tests.backtest.support import HOUR, directional_signal

START = datetime(2024, 6, 1, tzinfo=UTC)


def _series(
    start: datetime,
    count: int,
    *,
    closes: tuple[int, ...] | None = None,
    opens: tuple[int, ...] | None = None,
    shas: tuple[str, ...] | None = None,
) -> tuple[MarketBar, ...]:
    rows = []
    for offset in range(count):
        close = 100 + offset if closes is None else closes[offset]
        raw_open = 50 if opens is None else opens[offset]
        sha = "a" * 64 if shas is None else shas[offset]
        rows.append(
            synthetic_market_bar(
                start + offset * HOUR,
                raw_open,
                close=close,
                high=max(raw_open, close),
                low=min(raw_open, close),
                archive_sha=sha,
            )
        )
    return tuple(rows)


def _signals(bars: tuple[MarketBar, ...], lookback: int = 1):
    observations = tuple(
        PriceObservation(
            open_time=bar.open_time,
            close_time=bar.close_time,
            close=bar.close,
            source_archive_sha256=bar.source_archive_sha256,
            source_year=bar.source_year,
            source_month=bar.source_month,
            source_schema_version=bar.source_schema_version,
        )
        for bar in bars
    )
    return MomentumStrategy(MomentumConfig(lookback_hours=lookback)).generate(
        observations
    )


def test_momentum_fill_uses_the_later_open_and_repeated_long_is_a_no_op():
    bars = _series(START, 6)
    result = BacktestEngine().run_synthetic(bars, _signals(bars))
    buy_fills = [fill for fill in result.fills if fill.side.value == "buy"]

    assert len(buy_fills) == 1
    assert buy_fills[0].time == START + 3 * HOUR
    assert buy_fills[0].raw_open == Decimal(50)
    assert buy_fills[0].execution_price == Decimal(50) * Decimal("1.0005")
    assert buy_fills[0].execution_price != bars[3].close
    assert result.final_btc > 0
    assert result.final_cash >= 0
    assert result.final_cash + buy_fills[0].posted_notional + buy_fills[
        0
    ].posted_fee == Decimal(10000)


def test_month_boundary_keeps_both_source_hashes():
    start = datetime(2023, 12, 31, 22, tzinfo=UTC)
    december = "a" * 64
    january = "b" * 64
    bars = _series(
        start,
        5,
        shas=(december, december, january, january, january),
    )
    result = BacktestEngine().run_synthetic(bars, _signals(bars))

    assert result.fills[0].source_archive_sha256 == january
    assert result.fills[0].source_month == 1
    assert result.fills[0].time == datetime(2024, 1, 1, 1, tzinfo=UTC)
    signal_sources = _signals(bars)[1].sources
    assert {source.source_archive_sha256 for source in signal_sources} == {december}


def test_train_window_does_not_fill_inside_validation():
    start = datetime(2023, 12, 31, 22, tzinfo=UTC)
    bars = _series(start, 5)
    signals = _signals(bars)
    engine = BacktestEngine()
    train = engine.run_synthetic(
        bars,
        signals,
        window=EvaluationWindow(TRAIN_START, VALIDATION_START),
    )
    validation = engine.run_synthetic(
        bars,
        signals,
        window=EvaluationWindow(VALIDATION_START, HOLDOUT_START),
    )

    assert train.fills == ()
    assert train.final_cash == Decimal(10000)
    assert train.final_btc == Decimal(0)
    assert validation.fills
    assert all(fill.time >= VALIDATION_START for fill in validation.fills)
    assert all(fill.time < HOLDOUT_START for fill in validation.fills)
    assert {fill.time for fill in train.fills}.isdisjoint(
        {fill.time for fill in validation.fills}
    )


def test_gap_does_not_invent_prices_and_a_held_position_is_marked_stale():
    start = START
    times = (
        start,
        start + HOUR,
        start + 2 * HOUR,
        start + 3 * HOUR,
        start + 6 * HOUR,
    )
    bars = tuple(
        synthetic_market_bar(
            when,
            50,
            close=100 + offset,
            high=100 + offset,
            low=50,
        )
        for offset, when in enumerate(times)
    )
    result = BacktestEngine().run_synthetic(bars, _signals(bars))
    equity_times = {point.time for point in result.equity_points}

    resumed = next(
        point for point in result.equity_points if point.time == start + 6 * HOUR
    )
    assert result.final_btc > 0
    assert start + 5 * HOUR not in equity_times
    assert all(point.fresh for point in result.equity_points)
    assert resumed.price_kind is PriceKind.OPEN
    assert resumed.price == Decimal(50)
    assert result.market_gaps == (
        type(result.market_gaps[0])(start + 3 * HOUR, start + 6 * HOUR),
    )
    assert result.stale_valuations == ()
    assert all(mark.time not in equity_times for mark in result.stale_valuations)
    assert result.no_signal_policy == "hold_and_cancel_pending"


def test_repeated_runs_have_the_same_identifier_and_fee_changes_it():
    bars = _series(START, 4)
    signals = _signals(bars)
    engine = BacktestEngine()
    first = engine.run_synthetic(bars, signals)
    second = engine.run_synthetic(bars, signals)
    changed = engine.run_synthetic(
        bars,
        signals,
        assumptions=ExecutionAssumptions(fee_rate=Decimal("0.002")),
    )

    assert first.run_id == second.run_id
    assert first.fills == second.fills
    assert changed.run_id != first.run_id
    assert "T" not in first.run_id


def test_latency_zero_and_off_grid_cash_are_rejected():
    try:
        ExecutionAssumptions(latency_bars=0)
    except BacktestConfigurationError as error:
        assert "latency" in str(error)
    else:
        raise AssertionError("zero latency was accepted")

    try:
        ExecutionAssumptions(initial_cash=Decimal("10000.000000001"))
    except BacktestConfigurationError as error:
        assert "cash_quantum" in str(error)
    else:
        raise AssertionError("off-grid cash was accepted")


def test_run_identity_binds_prices_and_signals():
    bars = _series(START, 4)
    signals = _signals(bars)
    engine = BacktestEngine()
    baseline = engine.run_synthetic(bars, signals)
    changed_high = (
        synthetic_market_bar(
            bars[0].open_time,
            bars[0].open,
            close=bars[0].close,
            high=bars[0].high + 1,
            low=bars[0].low,
            volume=bars[0].volume,
            archive_sha=bars[0].source_archive_sha256,
        ),
        *bars[1:],
    )
    changed_open = _series(START, 4, opens=(51, 50, 50, 50))
    flat = (directional_signal(bars[1].open_time, SignalAction.FLAT, "0"),)

    assert engine.run_synthetic(changed_high, signals).run_id != baseline.run_id
    assert engine.run_synthetic(changed_open, _signals(changed_open)).run_id != (
        baseline.run_id
    )
    assert engine.run_synthetic(bars, flat).run_id != baseline.run_id
    assert engine.run_synthetic(bars, signals).fills == baseline.fills


def test_conflicting_signal_identity_fails_before_a_fill():
    bars = _series(START, 3)
    signal = directional_signal(
        bars[0].open_time,
        SignalAction.LONG,
        "0.1",
        archive_sha="b" * 64,
    )

    try:
        BacktestEngine().run_synthetic(bars, (signal,))
    except BacktestDataError as error:
        assert "decision source" in str(error)
    else:
        raise AssertionError("mismatched signal identity produced a result")
