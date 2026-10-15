"""Independent oracles for volatility, Sharpe, Sortino, and drawdown."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from quantforge.analytics.models import UndefinedReason
from quantforge.analytics.returns import equity_returns
from quantforge.analytics.risk import (
    annualized_volatility,
    downside_mean_square,
    maximum_drawdown,
    sharpe_ratio,
    sortino_ratio,
    total_return,
)
from quantforge.backtest.models import EquityPoint, PriceKind
from quantforge.research.returns import return_context
from quantforge.research.statistics import summarize_returns

HOUR = timedelta(hours=1)
START = datetime(2024, 6, 1, tzinfo=UTC)


def _point(offset: int, equity: str) -> EquityPoint:
    amount = Decimal(equity)
    when = START + offset * HOUR
    return EquityPoint(
        time=when,
        price=amount,
        price_kind=PriceKind.CLOSE,
        price_open_time=when - HOUR,
        cash=amount,
        btc=Decimal(0),
        equity=amount,
        fresh=True,
        in_evaluation=True,
    )


def _known_path():
    points = (
        _point(0, "100"),
        _point(1, "110"),
        _point(2, "121"),
        _point(3, "108.9"),
    )
    return points, equity_returns(points)


def test_known_path_matches_the_hand_calculation():
    points, returns = _known_path()
    scale = math.sqrt(8760)
    deviation = math.sqrt(1 / 75)
    sharpe = (1 / 30) / deviation * scale
    sortino = (math.sqrt(3) / 3) * scale
    values = [Decimal("0.1"), Decimal("0.1"), Decimal("-0.1")]

    with localcontext(return_context()):
        assert downside_mean_square(values) == Decimal("0.01") / 3
        assert total_return(points, hourly_grid_complete=True).value == Decimal("0.089")
    assert maximum_drawdown(points, hourly_grid_complete=True).value == Decimal("-0.1")
    assert annualized_volatility(returns).value == pytest.approx(deviation * scale)
    assert sharpe_ratio(returns).value == pytest.approx(sharpe)
    assert sortino_ratio(returns).value == pytest.approx(sortino)
    assert sortino_ratio(returns).reason is None


def test_multiple_negative_returns_use_root_mean_square_not_sample_std():
    values = [Decimal("-0.1"), Decimal("-0.2"), Decimal("0.3")]
    clipped = [Decimal("-0.1"), Decimal("-0.2"), Decimal(0)]
    mean_square = downside_mean_square(values)
    clipped_std = summarize_returns(clipped).sample_standard_deviation
    result = sortino_ratio(_series(values))

    with localcontext(return_context()):
        assert mean_square == (Decimal("0.01") + Decimal("0.04")) / 3
    assert float(mean_square.sqrt()) != pytest.approx(clipped_std)
    assert result.value == pytest.approx(0.0)
    assert result.reason is None


def test_flat_constant_and_all_positive_series_have_explicit_undefined_ratios():
    flat_points = (_point(0, "10000"), _point(1, "10000"), _point(2, "10000"))
    flat = equity_returns(flat_points)
    positive = _series([Decimal("0.01"), Decimal("0.02")])
    steady = _series([Decimal("0.01"), Decimal("0.01")])
    negative = _series([Decimal("-0.1"), Decimal("-0.1")])

    assert total_return(flat_points, hourly_grid_complete=True).value == Decimal(0)
    assert maximum_drawdown(flat_points, hourly_grid_complete=True).value == Decimal(0)
    assert annualized_volatility(flat).value == pytest.approx(0.0)
    assert sharpe_ratio(flat).reason is UndefinedReason.ZERO_DISPERSION
    assert sortino_ratio(flat).reason is UndefinedReason.ZERO_DOWNSIDE_DEVIATION
    assert sharpe_ratio(steady).reason is UndefinedReason.ZERO_DISPERSION
    assert sortino_ratio(positive).reason is UndefinedReason.ZERO_DOWNSIDE_DEVIATION
    assert sharpe_ratio(positive).reason is None
    assert sortino_ratio(negative).value == pytest.approx(-math.sqrt(8760))
    assert sharpe_ratio(negative).reason is UndefinedReason.ZERO_DISPERSION


def test_gaps_and_short_samples_do_not_invent_dispersion():
    points = (_point(0, "100"), _point(1, "110"), _point(3, "110"))
    returns = equity_returns(points)

    assert sharpe_ratio(returns).reason is UndefinedReason.INCOMPLETE_HOURLY_GRID
    assert sortino_ratio(returns).reason is UndefinedReason.INCOMPLETE_HOURLY_GRID
    assert (
        annualized_volatility(returns).reason is UndefinedReason.INCOMPLETE_HOURLY_GRID
    )
    assert total_return(points, hourly_grid_complete=False).value == Decimal("0.1")
    assert (
        total_return(points, hourly_grid_complete=False).coverage
        == "observed_endpoints"
    )
    assert maximum_drawdown(points, hourly_grid_complete=False).coverage == (
        "observed_points_with_gaps"
    )
    assert sharpe_ratio(equity_returns(points[:2])).reason is (
        UndefinedReason.INSUFFICIENT_OBSERVATIONS
    )
    assert maximum_drawdown((), hourly_grid_complete=False).reason is (
        UndefinedReason.INSUFFICIENT_OBSERVATIONS
    )


def test_binary64_collapse_is_undefined_rather_than_zero():
    base = Decimal(2**53)
    returns = _series([base, base + 1, base + 2])

    assert annualized_volatility(returns).reason is UndefinedReason.BINARY64_UNDEFINED
    assert sharpe_ratio(returns).reason is UndefinedReason.BINARY64_UNDEFINED
    assert sortino_ratio(returns).reason is UndefinedReason.BINARY64_UNDEFINED


def _series(values: list[Decimal]):
    from quantforge.analytics.models import PortfolioReturn

    rows = [
        PortfolioReturn(
            time=START,
            previous_time=START,
            value=None,
            missing_reason="initial",
            previous_role="initial",
        )
    ]
    previous = START
    for index, value in enumerate(values, start=1):
        when = START + index * HOUR
        rows.append(
            PortfolioReturn(
                time=when,
                previous_time=previous,
                value=value,
                missing_reason=None,
                previous_role="evaluation",
            )
        )
        previous = when
    return tuple(rows)
