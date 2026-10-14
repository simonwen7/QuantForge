"""Trailing descriptive statistics with explicit complete-window rules.

A rolling result at index ``i`` uses only ``values[: i + 1]`` and is available
at the same time as input ``i``. For a close-to-close return, that time is the
current candle's ``available_at``. These functions do not assign a new
timestamp. Standard deviation, volatility, autocorrelation, and quantiles cross
into binary64 through :func:`decimal_to_float`. A successive nonzero gap is
undefined when it is no larger than 1000 binary64 roundoffs of the local
magnitude, using a roundoff of ``1e-15``, or when the converted gap differs
from the Decimal gap by more than a relative tolerance of ``1e-3``. Pearson
correlations whose absolute value exceeds 1 by more than ``1e-10`` are
undefined. Smaller floating-point excesses are clamped to the closed interval
``[-1, 1]``.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from decimal import Decimal, localcontext

from quantforge.research.models import (
    DEFAULT_PERIODS_PER_YEAR,
    DescriptiveSummary,
    MissingReason,
    StatisticError,
    StatisticPoint,
)
from quantforge.research.returns import return_context

BINARY64_UNIT_ROUNDOFF = Decimal("1e-15")
BINARY64_MINIMUM_GAP_ULPS = Decimal(1000)
BINARY64_GAP_RELATIVE_TOLERANCE = Decimal("1e-3")
BINARY64_CORRELATION_TOLERANCE = 1e-10


def decimal_to_float(value: Decimal) -> float:
    """Convert one finite Decimal at the explicit binary64 boundary.

    Values that overflow to a non-finite float, or underflow to zero, are
    rejected. Callers that can omit one window should treat that rejection as
    an undefined estimate rather than substitute zero.
    """
    if not isinstance(value, Decimal):
        raise StatisticError("statistical estimation requires Decimal inputs")
    if not value.is_finite():
        raise StatisticError("statistical estimation requires finite Decimal inputs")
    converted = float(value)
    if not math.isfinite(converted) or (converted == 0.0 and value != 0):
        raise StatisticError("Decimal value has no meaningful binary64 representation")
    return converted


def _binary64(values: Sequence[Decimal]) -> list[float] | None:
    """Return binary64 values, or None when their variation would be distorted."""
    converted: list[float] = []
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise StatisticError(
                "statistical estimation requires finite Decimal inputs"
            )
        try:
            converted.append(decimal_to_float(value))
        except StatisticError as error:
            if "binary64" not in str(error):
                raise
            return None
    pairs = zip(
        itertools.pairwise(values),
        itertools.pairwise(converted),
        strict=True,
    )
    for (left, right), (left_float, right_float) in pairs:
        gap = right - left
        float_gap = right_float - left_float
        if gap == 0:
            if float_gap != 0.0:
                return None
            continue
        magnitude = max(abs(left), abs(right))
        minimum_gap = magnitude * BINARY64_UNIT_ROUNDOFF * BINARY64_MINIMUM_GAP_ULPS
        if abs(gap) <= minimum_gap or float_gap == 0.0 or not math.isfinite(float_gap):
            return None
        recovered = Decimal(float_gap)
        if abs(recovered - gap) > abs(gap) * BINARY64_GAP_RELATIVE_TOLERANCE:
            return None
    return converted


def _present(value: Decimal | None) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _points(
    values: Sequence[Decimal | None],
    window: int,
    calculator,
) -> tuple[StatisticPoint, ...]:
    if window < 1:
        raise StatisticError("window must be positive")
    results: list[StatisticPoint] = []
    for index in range(len(values)):
        if index < window - 1:
            results.append(StatisticPoint(None, MissingReason.INCOMPLETE_WINDOW))
            continue
        window_values = values[index - window + 1 : index + 1]
        if len(window_values) != window or not all(
            _present(value) for value in window_values
        ):
            results.append(StatisticPoint(None, MissingReason.INCOMPLETE_WINDOW))
            continue
        decimals = [value for value in window_values if isinstance(value, Decimal)]
        calculated = calculator(decimals)
        if calculated is None:
            results.append(StatisticPoint(None, MissingReason.UNDEFINED))
        else:
            results.append(StatisticPoint(calculated, None))
    return tuple(results)


def _decimal_mean(values: Sequence[Decimal]) -> Decimal:
    with localcontext(return_context()):
        return sum(values, Decimal(0)) / Decimal(len(values))


def rolling_mean(
    values: Sequence[Decimal | None],
    window: int,
) -> tuple[StatisticPoint, ...]:
    """Calculate a trailing mean only for complete windows."""
    return _points(values, window, _decimal_mean)


def _variance(values: Sequence[Decimal], ddof: int) -> float | None:
    if ddof not in (0, 1):
        raise StatisticError("ddof must be 0 or 1")
    if len(values) - ddof <= 0:
        raise StatisticError("standard deviation requires more observations than ddof")
    floats = _binary64(values)
    if floats is None:
        return None
    mean = math.fsum(floats) / len(floats)
    corrected = math.fsum((value - mean) ** 2 for value in floats)
    if not math.isfinite(corrected) or corrected < 0.0:
        return None
    return corrected / (len(floats) - ddof)


def rolling_standard_deviation(
    values: Sequence[Decimal | None],
    window: int,
    *,
    ddof: int = 1,
) -> tuple[StatisticPoint, ...]:
    """Calculate trailing sample standard deviation by default.

    ``ddof=1`` uses the sample denominator ``n - 1``. ``ddof=0`` explicitly
    selects the population denominator. Incomplete windows produce no value.
    """
    if window < 2:
        raise StatisticError("standard-deviation window must be at least 2")

    def calculate(window_values: Sequence[Decimal]) -> float | None:
        variance = _variance(window_values, ddof)
        if variance is None:
            return None
        deviation = math.sqrt(variance)
        if not math.isfinite(deviation):
            return None
        return deviation

    return _points(values, window, calculate)


def rolling_volatility(
    values: Sequence[Decimal | None],
    window: int,
    *,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
    ddof: int = 1,
) -> tuple[StatisticPoint, ...]:
    """Annualize trailing standard deviation by an explicit period count.

    The default factor of 8760 is a 365-day hourly convention. It is not the
    actual number of hourly observations in a leap year or in every calendar year.
    """
    if periods_per_year <= 0:
        raise StatisticError("periods_per_year must be positive")
    scale = math.sqrt(periods_per_year)
    return tuple(
        StatisticPoint(point.value * scale, None)
        if isinstance(point.value, float)
        else point
        for point in rolling_standard_deviation(values, window, ddof=ddof)
    )


def autocorrelation(
    values: Sequence[Decimal | None],
    lag: int,
) -> float | None:
    """Return Pearson autocorrelation, or None when it is undefined.

    The two lag-aligned samples must both be complete. The denominator uses
    centered sums of squares, so sample and population scaling cancel. A
    constant series, a series whose variation does not survive binary64, or a
    correlation more than ``1e-10`` outside ``[-1, 1]`` is undefined rather
    than reported as zero.
    """
    if lag < 1:
        raise StatisticError("autocorrelation lag must be positive")
    if any(not _present(value) for value in values):
        raise StatisticError("autocorrelation requires a complete series")
    if len(values) < lag + 2:
        raise StatisticError("autocorrelation requires at least lag + 2 observations")
    floats = _binary64([value for value in values if isinstance(value, Decimal)])
    if floats is None:
        return None
    left = floats[:-lag]
    right = floats[lag:]
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    numerator = math.fsum(
        (lhs - left_mean) * (rhs - right_mean)
        for lhs, rhs in zip(left, right, strict=True)
    )
    left_scale = math.fsum((value - left_mean) ** 2 for value in left)
    right_scale = math.fsum((value - right_mean) ** 2 for value in right)
    if (
        left_scale == 0.0
        or right_scale == 0.0
        or not math.isfinite(left_scale)
        or not math.isfinite(right_scale)
        or not math.isfinite(numerator)
    ):
        return None
    correlation = numerator / math.sqrt(left_scale * right_scale)
    if not math.isfinite(correlation):
        return None
    if abs(correlation) > 1.0 + BINARY64_CORRELATION_TOLERANCE:
        return None
    return min(1.0, max(-1.0, correlation))


def empirical_quantile(values: Sequence[Decimal], quantile: float) -> float:
    """Calculate a linear empirical quantile (Hyndman-Fan type 7)."""
    if not values:
        raise StatisticError("quantile requires observations")
    if not 0 <= quantile <= 1:
        raise StatisticError("quantile must be between 0 and 1")
    converted = _binary64(values)
    if converted is None:
        raise StatisticError(
            "quantile inputs have no meaningful binary64 representation"
        )
    ordered = sorted(converted)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_returns(values: Sequence[Decimal]) -> DescriptiveSummary:
    """Summarize one complete return sample without dropping observations."""
    if not values:
        raise StatisticError("summary requires observations")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise StatisticError("summary requires complete finite Decimal observations")
    variance = _variance(values, 1)
    if variance is None:
        raise StatisticError(
            "summary standard deviation has no meaningful binary64 representation"
        )
    deviation = math.sqrt(variance)
    if not math.isfinite(deviation):
        raise StatisticError(
            "summary standard deviation has no meaningful binary64 representation"
        )
    return DescriptiveSummary(
        count=len(values),
        mean=_decimal_mean(values),
        sample_standard_deviation=deviation,
        minimum=min(values),
        maximum=max(values),
        quantile_25=empirical_quantile(values, 0.25),
        median=empirical_quantile(values, 0.5),
        quantile_75=empirical_quantile(values, 0.75),
    )
