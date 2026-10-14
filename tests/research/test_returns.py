"""Tests for Decimal return definitions and missing-hour behavior."""

from datetime import UTC, datetime, timedelta
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from fractions import Fraction

import pytest

from quantforge.research import (
    LookaheadError,
    MissingReason,
    ResearchDataError,
    ReturnCalculationError,
    ReturnKind,
    assert_feature_available,
    calculate_returns,
    log_return,
    simple_return,
)
from tests.research.support import hourly_prices, price_observation

_REFERENCE_PRECISION = 90


def _high_precision_log(previous: Decimal, current: Decimal) -> Decimal:
    """Independent ln(current) - ln(previous) above the implementation precision."""
    context = Context(prec=_REFERENCE_PRECISION)
    context.traps[InvalidOperation] = True
    context.traps[DivisionByZero] = True
    context.traps[Overflow] = True
    with localcontext(context):
        return current.ln() - previous.ln()


def _series_ln2() -> Decimal:
    """Mercator series for ln(2), independent of Decimal.ln."""
    context = Context(prec=_REFERENCE_PRECISION)
    with localcontext(context):
        total = Decimal(0)
        for power in range(1, 400):
            term = Decimal(1) / (Decimal(power) * (Decimal(2) ** power))
            total += term
        return total


def _series_ln_1p(increment: Decimal) -> Decimal:
    """Taylor series for ln(1 + x), independent of Decimal.ln."""
    context = Context(prec=_REFERENCE_PRECISION)
    with localcontext(context):
        total = Decimal(0)
        power = Decimal(1)
        for term_number in range(1, 250):
            power *= increment
            term = power / Decimal(term_number)
            total += term if term_number % 2 else -term
        return total


def _assert_close(actual: Decimal, reference: Decimal) -> None:
    assert type(actual) is Decimal
    tolerance = max(abs(reference), Decimal("1e-30")) * Decimal("1e-40")
    assert abs(actual - reference) <= tolerance


def test_simple_and_log_returns_match_independent_references():
    assert simple_return(Decimal(100), Decimal(110)) == Decimal("0.1")
    assert simple_return(Decimal(110), Decimal(121)) == Decimal("0.1")
    two = _series_ln2()
    ten_percent = _series_ln_1p(Decimal("0.1"))
    tiny = _series_ln_1p(Decimal("1E-18"))
    with localcontext(Context(prec=_REFERENCE_PRECISION)):
        doubled = two * 2
    cases = (
        (Decimal(1), Decimal(2), two),
        (Decimal(2), Decimal(4), two),
        (Decimal(2), Decimal(8), doubled),
        (Decimal(100), Decimal(110), ten_percent),
        (Decimal(1), Decimal("1.000000000000000001"), tiny),
    )

    for previous, current, series in cases:
        high_precision = _high_precision_log(previous, current)
        _assert_close(high_precision, series)
        _assert_close(log_return(previous, current), series)

    assert log_return(Decimal(100), Decimal(110)) != simple_return(
        Decimal(100), Decimal(110)
    )


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (Decimal(100), Decimal(110)),
        (Decimal(3), Decimal(1)),
        (Decimal(1), Decimal("1.000000000000000001")),
        (Decimal(50), Decimal(50)),
    ],
)
def test_simple_return_matches_exact_fraction(previous: Decimal, current: Decimal):
    actual = simple_return(previous, current)
    expected = Fraction(current) / Fraction(previous) - 1

    assert type(actual) is Decimal
    assert abs(Fraction(actual) - expected) < Fraction(1, 10**40)


def test_constant_prices_have_zero_returns_and_tiny_changes_remain_nonzero():
    assert simple_return(Decimal(50), Decimal(50)) == Decimal(0)
    assert log_return(Decimal(50), Decimal(50)) == Decimal(0)

    tiny = simple_return(Decimal(1), Decimal("1.000000000000000001"))
    logarithmic = log_return(Decimal(1), Decimal("1.000000000000000001"))

    assert tiny == Decimal("1E-18")
    assert logarithmic > 0
    assert logarithmic < Decimal("1E-18")


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (Decimal(0), Decimal(1)),
        (Decimal(-1), Decimal(1)),
        (Decimal("NaN"), Decimal(1)),
        (Decimal("Infinity"), Decimal(1)),
        (Decimal(1), Decimal(0)),
    ],
)
def test_invalid_prices_are_rejected(previous, current):
    with pytest.raises(ReturnCalculationError):
        simple_return(previous, current)
    with pytest.raises(ReturnCalculationError):
        log_return(previous, current)


def test_binary_float_prices_are_rejected():
    with pytest.raises(ReturnCalculationError):
        simple_return(1.0, 1.1)  # type: ignore[arg-type]
    with pytest.raises(ReturnCalculationError):
        log_return(1.0, 1.1)  # type: ignore[arg-type]


def test_initial_and_gap_returns_do_not_cross_missing_hours():
    first = datetime(2024, 5, 1, 10, tzinfo=UTC)
    observations = (
        price_observation(first, 100),
        price_observation(first + timedelta(hours=1), 110),
        price_observation(first + timedelta(hours=3), 121),
    )

    returns = calculate_returns(observations, ReturnKind.SIMPLE)

    assert [item.missing_reason for item in returns] == [
        MissingReason.INITIAL,
        None,
        MissingReason.GAP,
    ]
    assert returns[1].value == Decimal("0.1")
    assert returns[2].value is None
    assert returns[2].value != Decimal("0.21")
    assert returns[1].available_at == first + timedelta(hours=2)
    assert returns[2].available_at == observations[2].available_at
    with pytest.raises(LookaheadError):
        assert_feature_available(returns[1], returns[1].close_time)
    assert_feature_available(returns[1], returns[1].available_at)


def test_return_series_preserves_decimal_alignment():
    observations = hourly_prices(
        datetime(2024, 1, 1, tzinfo=UTC),
        (Decimal(100), Decimal(110), Decimal(121)),
    )

    returns = calculate_returns(observations, ReturnKind.LOG)

    assert returns[0].value is None
    assert returns[1].value == log_return(Decimal(100), Decimal(110))
    assert returns[2].value == log_return(Decimal(110), Decimal(121))
    assert returns[1].value is not None
    assert all(type(item.value) is Decimal for item in returns[1:])
    _assert_close(returns[1].value, _series_ln_1p(Decimal("0.1")))


def test_future_price_does_not_change_an_earlier_return():
    start = datetime(2024, 6, 1, 10, tzinfo=UTC)
    original = hourly_prices(start, (100, 110, 121))
    changed = (
        original[0],
        original[1],
        price_observation(original[2].open_time, 200),
    )

    original_returns = calculate_returns(original, ReturnKind.SIMPLE)
    changed_returns = calculate_returns(changed, ReturnKind.SIMPLE)

    assert original_returns[:2] == changed_returns[:2]
    assert original_returns[2] != changed_returns[2]


def test_non_hourly_and_non_chronological_observations_are_not_bridged():
    start = datetime(2024, 6, 1, 10, tzinfo=UTC)
    short_step = (
        price_observation(start, 100),
        price_observation(start + timedelta(hours=1, microseconds=-1), 110),
    )
    reversed_order = (
        price_observation(start + timedelta(hours=1), 110),
        price_observation(start, 100),
    )

    short_returns = calculate_returns(short_step, ReturnKind.LOG)
    assert short_returns[1].missing_reason is MissingReason.GAP
    assert short_returns[1].value is None
    with pytest.raises(ResearchDataError, match="chronological"):
        calculate_returns(reversed_order, ReturnKind.SIMPLE)
