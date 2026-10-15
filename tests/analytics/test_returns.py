"""Portfolio-return construction. Expected ratios are written out literally."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantforge.analytics.models import AnalyticsIntegrityError
from quantforge.analytics.returns import (
    equity_returns,
    performance_sample,
    sample_coverage,
)
from quantforge.backtest.models import EquityPoint, PriceKind, StaleValuation
from quantforge.research.protocol import VALIDATION_START

HOUR = timedelta(hours=1)
START = datetime(2024, 6, 1, tzinfo=UTC)


def _point(
    when: datetime,
    equity: str,
    *,
    fresh: bool = True,
    evaluation: bool = True,
) -> EquityPoint:
    amount = Decimal(equity)
    return EquityPoint(
        time=when,
        price=amount,
        price_kind=PriceKind.OPEN,
        price_open_time=when,
        cash=amount,
        btc=Decimal(0),
        equity=amount,
        fresh=fresh,
        in_evaluation=evaluation,
    )


def test_flat_path_and_known_simple_returns():
    points = (
        _point(START, "100"),
        _point(START + HOUR, "110"),
        _point(START + 2 * HOUR, "121"),
        _point(START + 3 * HOUR, "108.9"),
    )
    returns = equity_returns(points)
    valued = [item.value for item in returns if item.value is not None]

    assert returns[0].missing_reason == "initial"
    assert valued == [Decimal("0.1"), Decimal("0.1"), Decimal("-0.1")]
    assert points[-1].equity / points[0].equity - 1 == Decimal("0.089")


def test_a_multi_hour_gap_is_not_a_one_hour_return():
    points = (
        _point(START, "100"),
        _point(START + HOUR, "110"),
        _point(START + 3 * HOUR, "110"),
    )
    returns = equity_returns(points)
    coverage = sample_coverage(
        points,
        returns,
        evaluation_end=points[-1].time + timedelta(microseconds=1),
    )

    assert returns[1].value == Decimal("0.1")
    assert returns[2].value is None
    assert returns[2].missing_reason == "gap"
    assert coverage.hourly_grid_complete is False
    assert coverage.gap_count == 1
    assert len(coverage.segments) == 1
    assert coverage.segments[0].return_count == 1


def test_multiple_segments_are_labeled_without_being_joined():
    points = (
        _point(START, "100"),
        _point(START + HOUR, "110"),
        _point(START + 3 * HOUR, "110"),
        _point(START + 4 * HOUR, "121"),
    )
    returns = equity_returns(points)
    coverage = sample_coverage(
        points,
        returns,
        evaluation_end=points[-1].time + timedelta(microseconds=1),
    )

    assert [item.missing_reason for item in returns] == [
        "initial",
        None,
        "gap",
        None,
    ]
    assert coverage.segments[0].return_count == 1
    assert coverage.segments[1].return_count == 1
    assert coverage.hourly_grid_complete is False


def test_invalid_equity_and_timestamps_raise_before_a_return():
    with pytest.raises(AnalyticsIntegrityError, match="positive"):
        equity_returns((_point(START, "0"),))
    with pytest.raises(AnalyticsIntegrityError, match="positive"):
        equity_returns((_point(START, "-1"),))
    with pytest.raises(AnalyticsIntegrityError, match="increasing"):
        equity_returns((_point(START, "1"), _point(START, "2")))
    with pytest.raises(AnalyticsIntegrityError, match="increasing"):
        equity_returns((_point(START + HOUR, "1"), _point(START, "2")))
    with pytest.raises(AnalyticsIntegrityError, match="stale"):
        equity_returns((_point(START, "1", fresh=False),))
    stale = StaleValuation(
        time=START,
        last_price=Decimal(1),
        last_price_time=START,
        cash=Decimal(1),
        btc=Decimal(0),
    )
    with pytest.raises(AnalyticsIntegrityError, match="EquityPoint"):
        equity_returns((stale,))  # type: ignore[arg-type]


def test_training_marks_and_split_anchors_are_rejected():
    train = _point(START, "100", evaluation=False)
    later = _point(START + HOUR, "110")
    with pytest.raises(AnalyticsIntegrityError, match="training"):
        equity_returns((train, later))

    series = (
        _point(VALIDATION_START - 2 * HOUR, "100"),
        _point(VALIDATION_START - HOUR, "100"),
        _point(VALIDATION_START, "100"),
    )
    with pytest.raises(AnalyticsIntegrityError, match="split"):
        performance_sample(series, VALIDATION_START, split_start=VALIDATION_START)
    sample = performance_sample(series, VALIDATION_START)
    assert sample[0].time == VALIDATION_START - HOUR
    assert series[0] not in sample


def test_a_future_or_backward_price_source_is_rejected():
    future = _point(START, "100")
    object.__setattr__(future, "price_open_time", START + HOUR)
    backward = (
        _point(START, "100"),
        _point(START + HOUR, "110"),
    )
    object.__setattr__(backward[1], "price_open_time", START - 2 * HOUR)

    with pytest.raises(AnalyticsIntegrityError, match="later than the mark"):
        equity_returns((future,))
    with pytest.raises(AnalyticsIntegrityError, match="moved backward"):
        equity_returns(backward)


def test_isolated_2025_timestamps_are_accepted_by_the_return_function():
    start = datetime(2025, 6, 1, tzinfo=UTC)
    returns = equity_returns((_point(start, "10"), _point(start + HOUR, "11")))

    assert returns[1].value == Decimal("0.1")
