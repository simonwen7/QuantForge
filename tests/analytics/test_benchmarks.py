"""Cash and buy-and-hold share the M6 clock and the cost scenarios."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, localcontext

from quantforge.analytics.benchmarks import buy_and_hold_signals, entry_fill_time
from quantforge.analytics.models import (
    BENCHMARK_ADAPTER_MOMENTUM,
    DEFAULT_SCENARIOS,
)
from quantforge.analytics.report import (
    _observations,
    report_payload,
    run_synthetic_study,
)
from quantforge.backtest import BacktestEngine, ExecutionAssumptions
from quantforge.research import synthetic_market_bar
from quantforge.research.returns import return_context
from quantforge.strategies import MomentumConfig, MomentumStrategy

HOUR = timedelta(hours=1)
START = datetime(2022, 3, 1, tzinfo=UTC)


def _bars(closes: tuple[str, ...], opens: tuple[str, ...] | None = None):
    rows = []
    for index, close in enumerate(closes):
        raw_open = close if opens is None else opens[index]
        rows.append(synthetic_market_bar(START + index * HOUR, raw_open, close=close))
    return tuple(rows)


def _policy(report, scenario: str, name: str):
    row = next(item for item in report.scenarios if item.scenario.name == scenario)
    return next(item for item in row.policies if item.name == name)


def test_cash_is_unchanged_and_costs_scale_on_a_one_btc_purchase():
    bars = _bars(("100", "110", "100", "100"), ("100", "100", "100", "100"))
    config = MomentumConfig(lookback_hours=1)
    report = run_synthetic_study(bars, config=config, initial_cash=Decimal("0.0001"))
    signals = MomentumStrategy(config).generate(_observations(bars))

    for scenario in DEFAULT_SCENARIOS:
        cash = _policy(report, scenario.name, "cash")
        hold = _policy(report, scenario.name, "buy_and_hold")
        assert cash.fill_count == 0
        assert cash.posted_fees == Decimal(0)
        assert cash.exposure_fraction == Decimal(0)
        assert hold.fill_count == 1
        assert hold.signal_role == "adapter_not_observed"
        assert report.coverage.evaluation_start == entry_fill_time(signals)
        assert cash.metrics[0].value == Decimal(0)

    zero = _policy(report, "zero", "buy_and_hold")
    baseline = _policy(report, "baseline", "buy_and_hold")
    stressed = _policy(report, "pessimistic_2x", "buy_and_hold")
    baseline_fill = _hold_fill(bars, Decimal("0.001"), Decimal("0.0005"))
    stressed_fill = _hold_fill(bars, Decimal("0.002"), Decimal("0.001"))
    expected_quantity = _affordable(
        Decimal("0.0001"), Decimal(100), Decimal("0.0005"), Decimal("0.001")
    )

    assert zero.posted_fees == Decimal(0)
    assert baseline_fill.quantity == stressed_fill.quantity == expected_quantity
    assert baseline.posted_fees == _posted_fee(
        expected_quantity, Decimal(100), Decimal("0.0005"), Decimal("0.001")
    )
    assert stressed.posted_fees == _posted_fee(
        expected_quantity, Decimal(100), Decimal("0.001"), Decimal("0.002")
    )
    assert stressed.posted_fees > baseline.posted_fees
    payload = report_payload(report)
    assert "momentum_value" not in str(payload)
    assert "adapter_not_observed" in str(payload)


def test_a_flat_first_signal_does_not_delay_buy_and_hold():
    bars = _bars(("100", "90", "90", "90"))
    config = MomentumConfig(lookback_hours=1)
    strategy_signals = MomentumStrategy(config).generate(_observations(bars))
    hold_signals = buy_and_hold_signals(bars, strategy_signals)
    report = run_synthetic_study(bars, config=config)

    assert strategy_signals[1].action.value == "flat"
    assert entry_fill_time(hold_signals) == entry_fill_time(strategy_signals)
    assert report.coverage.evaluation_start == entry_fill_time(strategy_signals)
    assert _policy(report, "baseline", "momentum").fill_count == 0
    assert _policy(report, "baseline", "buy_and_hold").fill_count == 1
    assert all(
        signal.momentum is None or signal.momentum == BENCHMARK_ADAPTER_MOMENTUM
        for signal in hold_signals
    )


def _hold_fill(bars, fee: Decimal, slippage: Decimal):
    config = MomentumConfig(lookback_hours=1)
    signals = buy_and_hold_signals(
        bars,
        MomentumStrategy(config).generate(_observations(bars)),
    )
    result = BacktestEngine().run_synthetic(
        bars,
        signals,
        ExecutionAssumptions(
            initial_cash=Decimal("0.0001"),
            fee_rate=fee,
            slippage_rate=slippage,
        ),
    )
    return result.fills[0]


def _affordable(cash: Decimal, raw_open: Decimal, slippage: Decimal, fee: Decimal):
    quantum = Decimal("0.00000001")
    with localcontext(return_context()):
        price = raw_open * (Decimal(1) + slippage)
        raw = cash / (price * (Decimal(1) + fee))
        return (raw / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum


def _posted_fee(quantity: Decimal, raw_open: Decimal, slippage: Decimal, fee: Decimal):
    quantum = Decimal("0.00000001")
    with localcontext(return_context()):
        raw_fee = quantity * raw_open * (Decimal(1) + slippage) * fee
        return (raw_fee / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
