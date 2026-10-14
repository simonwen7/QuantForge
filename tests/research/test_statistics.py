"""Reference tests for trailing research statistics."""

import math
from decimal import Decimal

import pytest

from quantforge.research import (
    MissingReason,
    StatisticError,
    autocorrelation,
    decimal_to_float,
    rolling_mean,
    rolling_standard_deviation,
    rolling_volatility,
    summarize_returns,
)


def test_rolling_mean_uses_complete_trailing_windows_only():
    values = [Decimal(index) for index in (1, 2, 3, 4)]

    points = rolling_mean(values, 3)

    assert [point.missing_reason for point in points[:2]] == [
        MissingReason.INCOMPLETE_WINDOW,
        MissingReason.INCOMPLETE_WINDOW,
    ]
    assert points[2].value == Decimal(2)
    assert type(points[2].value) is Decimal
    assert points[3].value == Decimal(3)


def test_sample_and_population_standard_deviation_match_reference():
    values = [Decimal(index) for index in (1, 2, 3)]

    sample = rolling_standard_deviation(values, 3, ddof=1)
    population = rolling_standard_deviation(values, 3, ddof=0)

    assert sample[-1].value == pytest.approx(1.0)
    assert population[-1].value == pytest.approx(math.sqrt(2 / 3))


def test_missing_observation_invalidates_each_overlapping_window():
    values = [Decimal(1), None, Decimal(3), Decimal(4)]

    points = rolling_standard_deviation(values, 3)

    assert all(point.value is None for point in points)
    assert all(
        point.missing_reason is MissingReason.INCOMPLETE_WINDOW for point in points
    )


def test_volatility_uses_explicit_8760_convention():
    values = [Decimal(index) for index in (1, 2, 3)]

    points = rolling_volatility(values, 3)

    assert points[-1].value == pytest.approx(math.sqrt(8760))


def test_future_value_does_not_change_current_rolling_mean():
    original = [Decimal(index) for index in (1, 2, 3, 4)]
    changed_future = original.copy()
    changed_future[-1] = Decimal(100)

    original_points = rolling_mean(original, 3)
    changed_points = rolling_mean(changed_future, 3)

    assert original_points[2] == changed_points[2]
    assert original_points[3] != changed_points[3]


def test_autocorrelation_matches_hand_calculation_and_constant_series_is_undefined():
    values = [Decimal(index) for index in (1, 3, 2, 4)]
    # Lag-aligned samples are (1, 3, 2) and (3, 2, 4), whose Pearson correlation is -0.5.

    assert autocorrelation(values, 1) == pytest.approx(-0.5)
    assert autocorrelation([Decimal(5)] * 4, 1) is None


def test_autocorrelation_rejects_incomplete_or_short_series():
    with pytest.raises(StatisticError, match="complete"):
        autocorrelation([Decimal(1), None, Decimal(3)], 1)
    with pytest.raises(StatisticError, match="lag \\+ 2"):
        autocorrelation([Decimal(1), Decimal(2)], 1)


def test_large_offset_standard_deviation_remains_stable():
    base = Decimal(100_000_000)
    values = [base, base + Decimal(1), base + Decimal(2)]

    deviation = rolling_standard_deviation(values, 3)[-1].value

    assert deviation == pytest.approx(1.0)


def test_summary_quantiles_and_extrema_use_the_complete_sample():
    values = [Decimal(index) for index in (1, 2, 3, 4)]

    summary = summarize_returns(values)

    assert summary.count == 4
    assert summary.mean == Decimal("2.5")
    assert summary.minimum == Decimal(1)
    assert summary.maximum == Decimal(4)
    assert summary.quantile_25 == pytest.approx(1.75)
    assert summary.median == pytest.approx(2.5)
    assert summary.quantile_75 == pytest.approx(3.25)
    assert summary.sample_standard_deviation == pytest.approx(math.sqrt(5 / 3))


def _type7_quantile(values: list[float], quantile: float) -> float:
    """Independent Hyndman-Fan type-7 quantile using 1-based indexing."""
    ordered = sorted(values)
    position = 1 + (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower - 1] * (1 - weight) + ordered[upper - 1] * weight


def test_quantiles_match_an_independent_type7_implementation():
    from quantforge.research.statistics import empirical_quantile

    values = [Decimal(value) for value in (4, 1, 3, 2, 10)]
    reference = [float(value) for value in (1, 2, 3, 4, 10)]

    for probability in (0, 0.25, 0.5, 0.75, 1):
        assert empirical_quantile(values, probability) == pytest.approx(
            _type7_quantile(reference, probability)
        )


def test_mixed_sign_small_and_constant_dispersion_have_closed_forms():
    mixed = [Decimal(value) for value in (-1, 0, 1)]
    small = [Decimal("1e-12"), Decimal("2e-12"), Decimal("3e-12")]
    nearly_constant = [
        Decimal(100),
        Decimal("100.000000001"),
        Decimal("100.000000002"),
    ]
    constant = [Decimal("0.25")] * 4

    assert rolling_standard_deviation(mixed, 3)[-1].value == pytest.approx(1.0)
    assert rolling_standard_deviation(small, 3)[-1].value == pytest.approx(1e-12)
    assert rolling_standard_deviation(nearly_constant, 3)[-1].value == pytest.approx(
        1e-9
    )
    assert rolling_standard_deviation(constant, 4)[-1].value == 0.0
    assert autocorrelation([Decimal(value) for value in (1, -1, 1, -1)], 1) == (
        pytest.approx(-1.0)
    )
    base = Decimal(100_000_000)
    shifted = [base + Decimal(value) for value in (0, 1, 2, 3)]
    assert autocorrelation(shifted, 1) == pytest.approx(1.0)


def test_binary64_collapse_is_undefined_rather_than_zero():
    base = Decimal(2**53)
    collapsed = [base, base + Decimal(1), base + Decimal(2)]

    deviation = rolling_standard_deviation(collapsed, 3)[-1]
    volatility = rolling_volatility(collapsed, 3)[-1]

    assert deviation.value is None
    assert deviation.missing_reason is MissingReason.UNDEFINED
    assert volatility.missing_reason is MissingReason.UNDEFINED
    assert autocorrelation(collapsed + [base + Decimal(3)], 1) is None
    with pytest.raises(StatisticError, match="binary64"):
        summarize_returns(collapsed)
    with pytest.raises(StatisticError, match="binary64"):
        decimal_to_float(Decimal("1e400"))
    with pytest.raises(StatisticError, match="binary64"):
        decimal_to_float(Decimal("1e-400"))


def test_return_windows_do_not_cross_gaps_or_future_values():
    from datetime import UTC, datetime

    from quantforge.research import ReturnKind, calculate_returns
    from tests.research.support import hourly_prices, price_observation

    start = datetime(2024, 6, 1, 10, tzinfo=UTC)
    gapped = (
        price_observation(start, 100),
        price_observation(start.replace(hour=11), 110),
        price_observation(start.replace(hour=12), 121),
        price_observation(start.replace(hour=14), 130),
    )
    gapped_points = rolling_mean(
        [item.value for item in calculate_returns(gapped, ReturnKind.SIMPLE)],
        2,
    )
    contiguous = hourly_prices(start, (100, 110, 121, Decimal("133.1")))
    original_points = rolling_mean(
        [item.value for item in calculate_returns(contiguous, ReturnKind.SIMPLE)],
        2,
    )
    changed = (
        contiguous[0],
        contiguous[1],
        contiguous[2],
        price_observation(contiguous[3].open_time, 400),
    )
    changed_points = rolling_mean(
        [item.value for item in calculate_returns(changed, ReturnKind.SIMPLE)],
        2,
    )

    assert gapped_points[2].value == Decimal("0.1")
    assert gapped_points[3].missing_reason is MissingReason.INCOMPLETE_WINDOW
    assert original_points[2] == changed_points[2]
    assert original_points[3] != changed_points[3]
