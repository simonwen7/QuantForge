"""M6 accepts mean-reversion signals without a fabricated momentum value."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantforge.backtest.engine import BacktestEngine, _signal_identity
from quantforge.backtest.models import (
    BacktestDataError,
    EvaluationWindow,
    ExecutionAssumptions,
    PriceKind,
    RejectionReason,
)
from quantforge.backtest.replay import prepare_signals
from quantforge.research.market_bars import synthetic_market_bar
from quantforge.research.models import PriceObservation
from quantforge.strategies.mean_reversion import (
    MeanReversionConfig,
    MeanReversionSignal,
    MeanReversionStrategy,
    mean_reversion_config_id,
)
from quantforge.strategies.models import (
    LookbackSource,
    NoSignalReason,
    SignalAction,
)
from quantforge.strategies.momentum import MomentumConfig, MomentumStrategy

START = datetime(2022, 6, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
TICK = timedelta(milliseconds=1)


def _bars(closes: list[str], opens: list[str] | None = None):
    rows = []
    for index, close in enumerate(closes):
        open_price = close if opens is None else opens[index]
        rows.append(synthetic_market_bar(START + index * HOUR, open_price, close=close))
    return tuple(rows)


def _observations(bars):
    return tuple(
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


def test_a_negative_deviation_buys_at_the_next_open_without_momentum():
    closes = ["100"] * 24 + ["97.99", "100", "100"]
    opens = ["100"] * 27
    bars = _bars(closes, opens)
    signals = MeanReversionStrategy().generate(_observations(bars))
    decision = signals[24]
    result = BacktestEngine().run_synthetic(
        bars,
        signals,
        ExecutionAssumptions(
            initial_cash=Decimal("100.1"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal(0),
        ),
    )
    identity = _signal_identity(decision)
    filled = result.fills[0]
    mark = next(point for point in result.equity_points if point.time == filled.time)

    assert decision.action is SignalAction.LONG
    assert decision.deviation < Decimal("-0.02")
    assert "deviation" in identity
    assert "momentum" not in identity
    assert filled.time == START + 26 * HOUR
    assert filled.raw_open == Decimal(100)
    assert filled.raw_open != Decimal("97.99")
    assert filled.posted_fee == Decimal("0.1")
    assert filled.quantity == Decimal(1)
    assert mark.price_kind is PriceKind.OPEN
    assert mark.price == Decimal(100)
    assert mark.equity == Decimal(100)
    assert result.assumptions.initial_cash - mark.equity == filled.posted_fee


def test_momentum_identity_keeps_its_key_and_does_not_collide():
    bars = _bars(["100", "110", "110"])
    momentum = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(
        _observations(bars)
    )
    reversion = MeanReversionStrategy(
        MeanReversionConfig(lookback_hours=1, entry_threshold=Decimal("0.02"))
    ).generate(_observations(bars))
    engine = BacktestEngine()
    momentum_result = engine.run_synthetic(bars, momentum)
    reversion_result = engine.run_synthetic(bars, reversion)
    momentum_identity = _signal_identity(momentum[1])

    assert set(momentum_identity) == {
        "action",
        "available_at",
        "momentum",
        "no_signal_reason",
        "open_time",
        "strategy_config_id",
    }
    assert "deviation" not in momentum_identity
    assert momentum_result.run_id == engine.run_synthetic(bars, momentum).run_id
    assert momentum_result.run_id != reversion_result.run_id
    changed = _bars(["100", "110", "90"])
    changed_signals = MeanReversionStrategy(
        MeanReversionConfig(lookback_hours=1, entry_threshold=Decimal("0.02"))
    ).generate(_observations(changed))
    assert (
        engine.run_synthetic(changed, changed_signals).run_id != reversion_result.run_id
    )


def test_no_signal_holds_and_a_duck_typed_signal_is_rejected():
    closes = ["100"] * 24 + ["97", "97", "97"]
    bars = _bars(closes)
    signals = list(MeanReversionStrategy().generate(_observations(bars)))
    for index in (25, 26):
        hold = bars[index]
        signals[index] = _abstain(hold)
    result = BacktestEngine().run_synthetic(
        bars,
        signals,
        ExecutionAssumptions(fee_rate=Decimal(0), slippage_rate=Decimal(0)),
    )

    assert len(result.fills) == 1
    assert result.fills[0].side.value == "buy"
    assert result.final_btc > 0

    class Duck:
        action = SignalAction.LONG

    with pytest.raises(BacktestDataError, match="MeanReversionSignal"):
        prepare_signals([Duck()], bars)  # type: ignore[list-item]


def _abstain(hold):
    return MeanReversionSignal(
        open_time=hold.open_time,
        close_time=hold.close_time,
        available_at=hold.available_at,
        action=SignalAction.NO_SIGNAL,
        deviation=None,
        no_signal_reason=NoSignalReason.WARMUP,
        strategy_config_id=mean_reversion_config_id(MeanReversionConfig()),
        lookback_hours=24,
        entry_threshold=Decimal("0.02"),
        lookback_start=hold.open_time,
        lookback_end=hold.open_time,
        sources=(
            LookbackSource(
                hold.open_time,
                hold.source_year,
                hold.source_month,
                hold.source_archive_sha256,
                hold.source_schema_version,
            ),
        ),
        execution_policy=None,
        earliest_eligible_open=None,
    )


def test_an_order_due_at_the_fold_end_does_not_fill():
    closes = ["100"] * 24 + ["97", "100", "100", "100", "97", "100"]
    bars = _bars(closes)
    fold_start = START + 24 * HOUR
    fold_end = fold_start + 6 * HOUR
    signals = MeanReversionStrategy().generate(_observations(bars))
    result = BacktestEngine().run_synthetic(
        bars,
        signals,
        window=EvaluationWindow(fold_start, fold_end),
    )

    assert fold_end not in {fill.time for fill in result.fills}
    rejected = [
        order
        for order in result.orders
        if order.due_time == fold_end
        and order.reason is RejectionReason.OUTSIDE_EVALUATION_WINDOW
    ]
    assert rejected
    assert result.final_btc == Decimal(0) or all(
        fill.time < fold_end for fill in result.fills
    )
