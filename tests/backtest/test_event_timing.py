"""Causal event order, latency, and missing-bar behavior."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantforge.backtest import (
    BacktestEngine,
    EventPhase,
    ExecutionAssumptions,
    PriceKind,
)
from quantforge.backtest.models import BacktestDataError
from quantforge.research import synthetic_market_bar
from quantforge.strategies.models import SignalAction
from tests.backtest.support import HOUR, bars_at, directional_signal, warmup_signal

START = datetime(2024, 6, 1, 10, tzinfo=UTC)


def _phases(result, when):
    return [event.phase for event in result.events if event.time == when]


def test_phases_at_one_timestamp_follow_the_approved_order():
    bars = bars_at(
        START,
        ("10", "20", "100", "50"),
        ("10", "11", "999", "50"),
    )
    signals = (
        directional_signal(START, SignalAction.LONG, "0.1"),
        warmup_signal(START + HOUR),
    )
    result = BacktestEngine().run_synthetic(
        bars,
        signals,
        assumptions=ExecutionAssumptions(initial_cash=Decimal("100.15005")),
    )
    filled_at = START + 2 * HOUR
    phases = _phases(result, filled_at)

    assert phases == [
        EventPhase.BAR_OPEN,
        EventPhase.FILL_DUE,
        EventPhase.BAR_COMPLETE,
        EventPhase.SIGNAL,
        EventPhase.ORDER_SUBMISSION,
        EventPhase.VALUATION,
    ]
    open_event = next(
        event
        for event in result.events
        if event.phase is EventPhase.BAR_OPEN and event.time == filled_at
    )
    complete_event = next(
        event
        for event in result.events
        if event.phase is EventPhase.BAR_COMPLETE and event.time == filled_at
    )
    assert open_event.open_view is not None
    assert open_event.complete_view is None
    assert not hasattr(open_event.open_view, "high")
    assert not hasattr(open_event.open_view, "close")
    assert not hasattr(open_event.open_view, "volume")
    assert complete_event.complete_view is not None
    assert complete_event.complete_view.close == Decimal(11)
    assert result.fills[0].raw_open == Decimal(100)
    assert result.fills[0].execution_price == Decimal("100.05")
    marked = next(point for point in result.equity_points if point.time == filled_at)
    assert marked.price == Decimal(100)
    assert marked.price_kind is PriceKind.OPEN
    assert marked.price != complete_event.complete_view.close
    assert marked.fresh is True
    assert result.final_btc == Decimal(1)
    assert result.fills[0].time == filled_at
    assert result.orders[0].submitted_at == START + HOUR
    assert result.orders[0].due_time == filled_at


def test_completion_is_emitted_when_the_next_open_is_absent():
    bars = bars_at(START, ("10", "20"), ("10", "11"))
    result = BacktestEngine().run_synthetic(
        bars,
        (directional_signal(START, SignalAction.LONG, "0.1"),),
    )
    missing = START + 2 * HOUR
    names = [event.name for event in result.events if event.time == missing]

    assert "bar_open" not in names
    assert "bar_complete" in names
    assert "expire" in names
    assert result.fills == ()
    assert result.final_cash == Decimal(10000)
    assert result.orders[0].reason.value == "missing_execution_bar"
    completed = next(
        event
        for event in result.events
        if event.name == "bar_complete" and event.time == missing
    )
    assert completed.complete_view is not None
    assert completed.complete_view.open_time == START + HOUR


def test_signal_cannot_execute_before_available_at_or_use_a_future_close():
    bars = bars_at(
        START,
        ("10", "20", "30", "40"),
        ("10", "11", "12", "13"),
    )
    signal = directional_signal(START, SignalAction.LONG, "0.25")
    result = BacktestEngine().run_synthetic(bars, (signal,))
    fill_times = {fill.time for fill in result.fills}

    assert signal.available_at == START + HOUR
    assert fill_times == {START + 2 * HOUR}
    assert all(fill.time >= signal.available_at + HOUR for fill in result.fills)
    assert result.fills[0].raw_open == Decimal(30)
    assert result.fills[0].execution_price != Decimal(12)
    assert result.fills[0].execution_price != Decimal(13)


def test_future_close_and_high_do_not_change_an_earlier_fill():
    opens = ("10", "20", "30", "40", "50")
    closes = ("10", "11", "12", "13", "14")
    original = bars_at(START, opens, closes)
    changed = bars_at(
        START,
        opens,
        ("10", "11", "12", "13", "999"),
    )
    signals = (
        directional_signal(START, SignalAction.LONG, "0.1"),
        directional_signal(START + HOUR, SignalAction.FLAT, "0"),
    )
    engine = BacktestEngine()
    first = engine.run_synthetic(original, signals)
    second = engine.run_synthetic(changed, signals)

    assert first.fills == second.fills
    assert first.ledger == second.ledger
    assert first.fills[0].raw_open == Decimal(30)


def test_duplicate_timestamps_abort_before_a_fill():
    first = synthetic_market_bar(START, "10")
    duplicate = synthetic_market_bar(START, "11")

    with pytest.raises(BacktestDataError, match="duplicate or reversed"):
        BacktestEngine().run_synthetic(
            (first, duplicate),
            (directional_signal(START, SignalAction.LONG, "0.1"),),
        )


def test_microsecond_2025_completion_still_waits_one_hour():
    start = datetime(2025, 6, 1, 10, tzinfo=UTC)
    bars = bars_at(start, ("10", "20", "30"), ("10", "11", "12"))
    result = BacktestEngine().run_synthetic(
        bars,
        (directional_signal(start, SignalAction.LONG, "0.1"),),
    )

    assert bars[0].close_time == bars[0].available_at - timedelta(microseconds=1)
    assert result.fills[0].time == start + 2 * HOUR
    assert result.data_origin.value == "synthetic_unverified"


def test_fill_is_marked_at_the_execution_open_not_the_previous_close():
    bars = bars_at(
        START,
        ("100", "100", "100", "200"),
        ("100", "150", "100", "200"),
    )
    result = BacktestEngine().run_synthetic(
        bars,
        (directional_signal(START + HOUR, SignalAction.LONG, "0.5"),),
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal(200),
            fee_rate=Decimal(0),
            slippage_rate=Decimal(0),
        ),
    )
    filled_at = START + 3 * HOUR
    marked = next(point for point in result.equity_points if point.time == filled_at)
    later = next(
        point for point in result.equity_points if point.time == filled_at + HOUR
    )

    assert result.fills[0].time == filled_at
    assert result.fills[0].raw_open == Decimal(200)
    assert result.fills[0].execution_price == Decimal(200)
    assert result.fills[0].posted_fee == Decimal(0)
    assert marked.price_kind is PriceKind.OPEN
    assert marked.price == Decimal(200)
    assert marked.price != Decimal(100)
    assert marked.btc == Decimal(1)
    assert marked.equity == Decimal(200)
    assert later.price_kind is PriceKind.CLOSE
    assert later.price == Decimal(200)
    assert later.equity == Decimal(200)
    assert all(point.equity == Decimal(200) for point in result.equity_points)


def test_fill_mark_does_not_use_the_execution_bar_close():
    bars = bars_at(
        START,
        ("100", "100", "100", "200"),
        ("100", "150", "100", "999"),
    )
    result = BacktestEngine().run_synthetic(
        bars,
        (directional_signal(START + HOUR, SignalAction.LONG, "0.5"),),
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal(200),
            fee_rate=Decimal(0),
            slippage_rate=Decimal(0),
        ),
    )
    filled_at = START + 3 * HOUR
    marked = next(point for point in result.equity_points if point.time == filled_at)
    completed = next(
        point for point in result.equity_points if point.time == filled_at + HOUR
    )

    assert marked.price == Decimal(200)
    assert marked.equity == Decimal(200)
    assert completed.price == Decimal(999)
    assert completed.price_kind is PriceKind.CLOSE
    assert completed.time > result.fills[0].time


def test_an_open_is_the_mark_when_no_trade_occurs():
    bars = bars_at(START, ("100", "200"), ("100", "50"))
    result = BacktestEngine().run_synthetic(bars, ())
    opened = next(point for point in result.equity_points if point.time == START + HOUR)
    closed = next(
        point for point in result.equity_points if point.time == START + 2 * HOUR
    )

    assert result.fills == ()
    assert result.final_btc == Decimal(0)
    assert len({point.time for point in result.equity_points}) == len(
        result.equity_points
    )
    assert opened.price_kind is PriceKind.OPEN
    assert opened.price == Decimal(200)
    assert opened.price_open_time == START + HOUR
    assert opened.equity == Decimal(10000)
    assert closed.price_kind is PriceKind.CLOSE
    assert closed.price == Decimal(50)
    assert closed.price_open_time == START + HOUR
    assert closed.time > closed.price_open_time
    assert all(point.price_open_time <= point.time for point in result.equity_points)


def test_run_identity_binds_the_valuation_convention(monkeypatch):
    bars = bars_at(START, ("10", "11"), ("10", "12"))
    engine = BacktestEngine()
    original = engine.run_synthetic(bars, ())
    monkeypatch.setattr(
        "quantforge.backtest.engine.VALUATION_CONVENTION",
        "other-convention",
    )

    assert engine.run_synthetic(bars, ()).run_id != original.run_id
