"""End-to-end synthetic mechanics. These checks are not market results."""

from datetime import UTC, datetime
from decimal import Decimal

from quantforge.analytics.report import (
    demonstration_bars,
    render_markdown,
    run_synthetic_study,
)
from quantforge.analytics.returns import equity_returns, performance_sample
from quantforge.backtest import BacktestEngine, ExecutionAssumptions, PriceKind
from quantforge.strategies import MomentumConfig
from quantforge.strategies.models import SignalAction
from tests.backtest.support import HOUR, bars_at, directional_signal

START = datetime(2024, 6, 1, 10, tzinfo=UTC)


def test_zero_cost_open_mark_has_no_false_loss_and_the_fee_is_charged_once():
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
    sample = performance_sample(result.equity_points, filled_at)
    valued = [
        item.value
        for item in equity_returns(sample, opening_anchor=True)
        if item.value is not None
    ]
    marked = next(point for point in result.equity_points if point.time == filled_at)

    assert marked.price_kind is PriceKind.OPEN
    assert marked.price == Decimal(200)
    assert valued == [Decimal(0), Decimal(0)]
    assert result.fills[0].posted_fee == Decimal(0)

    costly_bars = bars_at(
        START,
        ("100", "100", "100", "100"),
        ("100", "150", "100", "100"),
    )
    costly = BacktestEngine().run_synthetic(
        costly_bars,
        (directional_signal(START + HOUR, SignalAction.LONG, "0.5"),),
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal("100.1"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal(0),
        ),
    )
    costly_fill = next(
        point for point in costly.equity_points if point.time == filled_at
    )
    anchor = next(
        point for point in costly.equity_points if point.time == filled_at - HOUR
    )

    assert costly.fills[0].raw_open == Decimal(100)
    assert costly.fills[0].posted_fee == Decimal("0.1")
    assert anchor.equity - costly_fill.equity == costly.fills[0].posted_fee
    assert costly_fill.equity == costly_fill.cash + costly_fill.btc * costly_fill.price
    assert costly_fill.price != Decimal(150)


def test_synthetic_demonstration_keeps_three_scenarios_and_excludes_warmup_returns():
    report = run_synthetic_study(demonstration_bars())
    markdown = render_markdown(report)
    momentum = next(
        policy for policy in report.scenarios[1].policies if policy.name == "momentum"
    )
    hold = next(
        policy
        for policy in report.scenarios[1].policies
        if policy.name == "buy_and_hold"
    )
    cash = next(
        policy for policy in report.scenarios[1].policies if policy.name == "cash"
    )

    assert report.data_origin == "synthetic_unverified"
    assert report.statement.startswith("SYNTHETIC MECHANICS DEMONSTRATION")
    assert report.coverage.hourly_grid_complete is True
    assert report.coverage.short_sample is True
    assert report.coverage.evaluation_start > report.coverage.opening_anchor_time
    assert cash.fill_count == 0
    assert cash.posted_fees == Decimal(0)
    assert hold.fill_count == 1
    assert momentum.fill_count == 2
    assert hold.posted_fees > cash.posted_fees
    assert "not actual BTCUSDT performance evidence" in markdown
    assert MomentumConfig().lookback_hours == 24
    run_ids = [
        policy.run_id for scenario in report.scenarios for policy in scenario.policies
    ]
    assert len(run_ids) == 9
    assert len(set(run_ids)) == 9
    assert report.coverage.open_mark_count > 0
    assert report.coverage.close_mark_count > 0
    assert report.return_convention == "hourly_asof_equity_mark"
    assert any("not a close-to-close" in warning for warning in report.warnings)
    assert any("both open marks" in warning for warning in report.warnings)


def test_fee_and_slippage_enter_marked_equity_separately():
    bars = bars_at(
        START,
        ("100", "100", "100", "100"),
        ("100", "150", "80", "100"),
    )
    signal = (directional_signal(START + HOUR, SignalAction.LONG, "0.5"),)
    filled_at = START + 3 * HOUR
    fee_only = BacktestEngine().run_synthetic(
        bars,
        signal,
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal("100.1"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal(0),
        ),
    )
    slip_only = BacktestEngine().run_synthetic(
        bars,
        signal,
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal("100.05"),
            fee_rate=Decimal(0),
            slippage_rate=Decimal("0.0005"),
        ),
    )
    both = BacktestEngine().run_synthetic(
        bars,
        signal,
        assumptions=ExecutionAssumptions(
            initial_cash=Decimal("100.15005"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0005"),
        ),
    )

    def at_fill(result):
        return next(point for point in result.equity_points if point.time == filled_at)

    fee_mark = at_fill(fee_only)
    slip_mark = at_fill(slip_only)
    both_mark = at_fill(both)
    assert fee_only.fills[0].quantity == Decimal(1)
    assert fee_only.fills[0].execution_price == Decimal(100)
    assert fee_only.fills[0].posted_fee == Decimal("0.1")
    assert fee_mark.price == Decimal(100)
    assert fee_mark.price != Decimal(80)
    assert fee_only.assumptions.initial_cash - fee_mark.equity == Decimal("0.1")
    assert slip_only.fills[0].posted_fee == Decimal(0)
    assert slip_only.fills[0].execution_price == Decimal("100.05")
    assert slip_only.assumptions.initial_cash - slip_mark.equity == Decimal("0.05")
    assert both.fills[0].posted_fee == Decimal("0.10005")
    assert both.fills[0].execution_price == Decimal("100.05")
    assert both_mark.price == Decimal(100)
    assert both_mark.equity == Decimal(100)
    assert both.assumptions.initial_cash - both_mark.equity == (
        both.fills[0].posted_fee
        + both.fills[0].execution_price
        - both.fills[0].raw_open
    )
