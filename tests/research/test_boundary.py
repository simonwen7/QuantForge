"""Terminal closes are valued at the exclusive end and are not fills."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.backtest.engine import BacktestEngine
from quantforge.backtest.models import (
    EvaluationWindow,
    ExecutionAssumptions,
    OrderState,
    RejectionReason,
)
from quantforge.research.boundary import (
    approved_partition_clocks,
    assert_no_following_bar,
    fill_due_exactly_at_end,
    partition_clock,
    select_terminal_mark,
)
from quantforge.research.market_bars import synthetic_market_bar
from quantforge.research.protocol import HOLDOUT_END, HOLDOUT_START, VALIDATION_START
from quantforge.strategies.models import (
    NEXT_BAR_OPEN,
    LookbackSource,
    NoSignalReason,
    SignalAction,
    StrategySignal,
)
from quantforge.strategies.momentum import MomentumConfig, strategy_config_id

HOUR = timedelta(hours=1)
CONFIG_ID = strategy_config_id(MomentumConfig(lookback_hours=1))


def test_approved_clocks_keep_the_last_close_inside_its_partition():
    train, validation, holdout = approved_partition_clocks()

    assert train.end == VALIDATION_START
    assert validation.end == HOLDOUT_START
    assert holdout.end == HOLDOUT_END
    assert holdout.last_open == datetime(2025, 12, 31, 23, tzinfo=UTC)
    assert holdout.last_close_time == datetime(
        2025, 12, 31, 23, 59, 59, 999999, tzinfo=UTC
    )
    assert holdout.terminal_mark_at == HOLDOUT_END
    assert train.last_close_time.microsecond == 999000
    assert validation.last_close_time.microsecond == 999000
    assert holdout.first_decision_open == HOLDOUT_START
    assert holdout.signal_available_at == HOLDOUT_START + HOUR
    assert holdout.earliest_fill_at == HOLDOUT_START + 2 * HOUR
    assert holdout.last_close_time < holdout.terminal_mark_at
    assert train.last_close_time < train.terminal_mark_at
    assert fill_due_exactly_at_end(holdout) == HOLDOUT_END - 2 * HOUR


def _bars(start: datetime, end: datetime):
    rows = []
    cursor = start
    while cursor < end:
        rows.append(
            synthetic_market_bar(cursor, "100", close="100", archive_sha="d" * 64)
        )
        cursor += HOUR
    return tuple(rows)


def _source(bar) -> LookbackSource:
    return LookbackSource(
        open_time=bar.open_time,
        source_year=bar.source_year,
        source_month=bar.source_month,
        source_archive_sha256=bar.source_archive_sha256,
        source_schema_version=bar.source_schema_version,
    )


def _signal(bar, previous, action: SignalAction) -> StrategySignal:
    abstaining = action is SignalAction.NO_SIGNAL
    return StrategySignal(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        action=action,
        momentum=None if abstaining else Decimal(1),
        no_signal_reason=NoSignalReason.WARMUP if abstaining else None,
        strategy_config_id=CONFIG_ID,
        lookback_hours=1,
        lookback_start=bar.open_time if abstaining else previous.open_time,
        lookback_end=bar.open_time,
        sources=(_source(bar),) if abstaining else (_source(previous), _source(bar)),
        execution_policy=None if abstaining else NEXT_BAR_OPEN,
        earliest_eligible_open=None if abstaining else bar.available_at,
    )


def _run(clock, bars, signals):
    assert_no_following_bar(bars, clock)
    return BacktestEngine().run_synthetic(
        bars,
        signals,
        ExecutionAssumptions(fee_rate=Decimal(0), slippage_rate=Decimal(0)),
        window=EvaluationWindow(clock.start, clock.end),
    )


def test_partition_edges_fill_only_inside_the_window(year_end):
    end = year_end
    start = end - 6 * HOUR
    clock = partition_clock("fixture", start, end)
    bars = _bars(start - 2 * HOUR, end)
    by_open = {bar.open_time: bar for bar in bars}
    gated = []
    for bar in bars:
        if bar.open_time < clock.start:
            gated.append(_signal(bar, None, SignalAction.NO_SIGNAL))
            continue
        previous = by_open[bar.open_time - HOUR]
        action = (
            SignalAction.LONG
            if bar.open_time == clock.first_decision_open
            else SignalAction.NO_SIGNAL
        )
        gated.append(_signal(bar, previous, action))
    result = _run(clock, bars, tuple(gated))
    last = by_open[clock.last_open]
    mark = select_terminal_mark(result.equity_points, clock, last)

    anchor = next(
        point for point in result.equity_points if point.time == clock.anchor_at
    )
    assert anchor.btc == Decimal(0)
    assert [fill.time for fill in result.fills] == [clock.earliest_fill_at]
    assert all(fill.time < clock.end for fill in result.fills)
    assert clock.start not in {fill.time for fill in result.fills}
    assert clock.end not in {fill.time for fill in result.fills}
    assert mark.time == clock.terminal_mark_at
    assert mark.in_evaluation is False
    assert mark.price == last.close
    completion = next(
        event.complete_view
        for event in result.events
        if event.complete_view is not None and event.time == clock.terminal_mark_at
    )
    assert completion.source_archive_sha256 == last.source_archive_sha256
    assert all(bar.open_time < clock.end for bar in bars)
    assert all(bar.open_time.year <= clock.last_open.year for bar in bars)


def test_an_order_due_at_the_exclusive_end_does_not_trade_or_liquidate():
    end = datetime(2024, 1, 1, tzinfo=UTC)
    start = end - 6 * HOUR
    clock = partition_clock("train-edge", start, end)
    bars = _bars(start, end)
    by_open = {bar.open_time: bar for bar in bars}
    decision_open = fill_due_exactly_at_end(clock)
    signals = []
    for bar in bars:
        previous = by_open.get(bar.open_time - HOUR)
        action = (
            SignalAction.LONG
            if bar.open_time == decision_open
            else SignalAction.NO_SIGNAL
        )
        signals.append(_signal(bar, previous, action))
    result = _run(clock, bars, tuple(signals))
    last = by_open[clock.last_open]
    mark = select_terminal_mark(result.equity_points, clock, last)
    rejected = next(order for order in result.orders if order.due_time == clock.end)

    assert result.fills == ()
    assert result.ledger == ()
    assert rejected.state is OrderState.REJECTED
    assert rejected.reason is RejectionReason.OUTSIDE_EVALUATION_WINDOW
    assert mark.cash == Decimal(10000)
    assert mark.btc == Decimal(0)
    assert mark.price_open_time == last.open_time


def pytest_generate_tests(metafunc):
    if "year_end" in metafunc.fixturenames:
        metafunc.parametrize(
            "year_end",
            [
                datetime(2024, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, tzinfo=UTC),
            ],
        )
