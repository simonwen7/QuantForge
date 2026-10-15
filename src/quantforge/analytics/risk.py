"""Risk metrics on a complete hourly equity-return sample.

Sharpe and volatility use the M4 sample standard deviation. Sortino does not.
Its denominator is the root-mean-square shortfall against a zero hourly target,
divided by the full return count, with no demeaning and no ``n - 1`` correction.
Annualized figures use ``sqrt(8760)`` and are not significance claims.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal, localcontext

from quantforge.analytics.models import (
    ANNUALIZATION_PERIODS,
    SORTINO_TARGET_HOURLY,
    AnalyticsIntegrityError,
    MetricResult,
    PortfolioReturn,
    UndefinedReason,
)
from quantforge.backtest.models import EquityPoint
from quantforge.research.models import MissingReason, StatisticError
from quantforge.research.returns import return_context, simple_return
from quantforge.research.statistics import decimal_to_float, summarize_returns

_ANNUALIZATION = math.sqrt(ANNUALIZATION_PERIODS)


def downside_mean_square(values: Sequence[Decimal]) -> Decimal:
    """Return ``sum(min(r - tau, 0)^2) / n`` with ``tau`` equal to zero."""
    if len(values) < 1:
        raise AnalyticsIntegrityError("downside deviation requires observations")
    with localcontext(return_context()):
        total = sum(
            (min(value - SORTINO_TARGET_HOURLY, Decimal(0))) ** 2 for value in values
        )
        return total / Decimal(len(values))


def total_return(
    points: Sequence[EquityPoint],
    *,
    hourly_grid_complete: bool,
) -> MetricResult:
    """Return the endpoint ratio, labeled by whether the hourly path is complete."""
    if len(points) < 2:
        return _undefined(
            "total_return",
            UndefinedReason.INSUFFICIENT_OBSERVATIONS,
            len(points),
            "simple_return",
            "observed_endpoints",
        )
    return MetricResult(
        name="total_return",
        value=simple_return(points[0].equity, points[-1].equity),
        unit="simple_return",
        reason=None,
        observation_count=len(points),
        coverage="complete_hourly" if hourly_grid_complete else "observed_endpoints",
    )


def maximum_drawdown(
    points: Sequence[EquityPoint],
    *,
    hourly_grid_complete: bool,
) -> MetricResult:
    """Return the most negative observed peak-to-trough ratio, or zero."""
    if not points:
        return _undefined(
            "maximum_drawdown",
            UndefinedReason.INSUFFICIENT_OBSERVATIONS,
            0,
            "simple_return",
            "observed_points",
        )
    peak = points[0].equity
    worst = Decimal(0)
    for point in points:
        peak = max(peak, point.equity)
        drawdown = simple_return(peak, point.equity)
        worst = min(worst, drawdown)
    coverage = (
        "observed_points_complete_grid"
        if hourly_grid_complete
        else "observed_points_with_gaps"
    )
    return MetricResult(
        name="maximum_drawdown",
        value=worst,
        unit="simple_return",
        reason=None,
        observation_count=len(points),
        coverage=coverage,
    )


def annualized_volatility(returns: Sequence[PortfolioReturn]) -> MetricResult:
    """Annualize the sample standard deviation of a complete hourly sample."""
    valued = _hourly_values(returns)
    if isinstance(valued, UndefinedReason):
        return _undefined(
            "annualized_volatility",
            valued,
            _count(returns),
            "annualized_stddev",
            "complete_hourly",
        )
    deviation = _sample_deviation(valued)
    if isinstance(deviation, UndefinedReason):
        return _undefined(
            "annualized_volatility",
            deviation,
            len(valued),
            "annualized_stddev",
            "complete_hourly",
        )
    return MetricResult(
        name="annualized_volatility",
        value=deviation * _ANNUALIZATION,
        unit="annualized_stddev",
        reason=None,
        observation_count=len(valued),
        coverage="complete_hourly",
    )


def sharpe_ratio(returns: Sequence[PortfolioReturn]) -> MetricResult:
    """Annualize ``mean(r) / sample_std(r)`` with a zero hourly risk-free rate."""
    valued = _hourly_values(returns)
    if isinstance(valued, UndefinedReason):
        return _undefined(
            "sharpe_ratio",
            valued,
            _count(returns),
            "annualized_ratio",
            "complete_hourly",
        )
    deviation = _sample_deviation(valued)
    if isinstance(deviation, UndefinedReason):
        return _undefined(
            "sharpe_ratio",
            deviation,
            len(valued),
            "annualized_ratio",
            "complete_hourly",
        )
    if deviation == 0.0:
        return _undefined(
            "sharpe_ratio",
            UndefinedReason.ZERO_DISPERSION,
            len(valued),
            "annualized_ratio",
            "complete_hourly",
        )
    mean = _mean(valued)
    if isinstance(mean, UndefinedReason):
        return _undefined(
            "sharpe_ratio", mean, len(valued), "annualized_ratio", "complete_hourly"
        )
    return _ratio("sharpe_ratio", mean, deviation, len(valued))


def sortino_ratio(returns: Sequence[PortfolioReturn]) -> MetricResult:
    """Annualize mean excess over the RMS shortfall. Zero shortfall is undefined."""
    valued = _hourly_values(returns)
    if isinstance(valued, UndefinedReason):
        return _undefined(
            "sortino_ratio",
            valued,
            _count(returns),
            "annualized_ratio",
            "complete_hourly",
        )
    if _sample_deviation(valued) is UndefinedReason.BINARY64_UNDEFINED:
        return _undefined(
            "sortino_ratio",
            UndefinedReason.BINARY64_UNDEFINED,
            len(valued),
            "annualized_ratio",
            "complete_hourly",
        )
    mean_square = downside_mean_square(valued)
    if mean_square == 0:
        return _undefined(
            "sortino_ratio",
            UndefinedReason.ZERO_DOWNSIDE_DEVIATION,
            len(valued),
            "annualized_ratio",
            "complete_hourly",
        )
    with localcontext(return_context()):
        deviation = mean_square.sqrt()
    try:
        converted = decimal_to_float(deviation)
    except StatisticError:
        return _undefined(
            "sortino_ratio",
            UndefinedReason.BINARY64_UNDEFINED,
            len(valued),
            "annualized_ratio",
            "complete_hourly",
        )
    mean = _mean(valued)
    if isinstance(mean, UndefinedReason):
        return _undefined(
            "sortino_ratio", mean, len(valued), "annualized_ratio", "complete_hourly"
        )
    return _ratio("sortino_ratio", mean, converted, len(valued))


def metric_set(
    points: Sequence[EquityPoint],
    returns: Sequence[PortfolioReturn],
    *,
    hourly_grid_complete: bool,
) -> tuple[MetricResult, ...]:
    """Return the five approved metrics in a stable order."""
    return (
        total_return(points, hourly_grid_complete=hourly_grid_complete),
        maximum_drawdown(points, hourly_grid_complete=hourly_grid_complete),
        annualized_volatility(returns),
        sharpe_ratio(returns),
        sortino_ratio(returns),
    )


def _hourly_values(
    returns: Sequence[PortfolioReturn],
) -> tuple[Decimal, ...] | UndefinedReason:
    values: list[Decimal] = []
    for item in returns:
        if item.missing_reason == MissingReason.INITIAL.value:
            continue
        if item.missing_reason is not None or item.value is None:
            return UndefinedReason.INCOMPLETE_HOURLY_GRID
        values.append(item.value)
    if len(values) < 2:
        return UndefinedReason.INSUFFICIENT_OBSERVATIONS
    return tuple(values)


def _sample_deviation(values: Sequence[Decimal]) -> float | UndefinedReason:
    try:
        summary = summarize_returns(values)
    except StatisticError as error:
        if "binary64" not in str(error):
            raise
        return UndefinedReason.BINARY64_UNDEFINED
    return summary.sample_standard_deviation


def _mean(values: Sequence[Decimal]) -> Decimal | UndefinedReason:
    try:
        summary = summarize_returns(values)
    except StatisticError as error:
        if "binary64" not in str(error):
            raise
        return UndefinedReason.BINARY64_UNDEFINED
    try:
        decimal_to_float(summary.mean)
    except StatisticError:
        return UndefinedReason.BINARY64_UNDEFINED
    return summary.mean


def _ratio(name: str, mean: Decimal, deviation: float, count: int) -> MetricResult:
    if deviation == 0.0:
        reason = (
            UndefinedReason.ZERO_DOWNSIDE_DEVIATION
            if name == "sortino_ratio"
            else UndefinedReason.ZERO_DISPERSION
        )
        return _undefined(name, reason, count, "annualized_ratio", "complete_hourly")
    value = decimal_to_float(mean) / deviation * _ANNUALIZATION
    if not math.isfinite(value):
        return _undefined(
            name,
            UndefinedReason.BINARY64_UNDEFINED,
            count,
            "annualized_ratio",
            "complete_hourly",
        )
    return MetricResult(
        name=name,
        value=value,
        unit="annualized_ratio",
        reason=None,
        observation_count=count,
        coverage="complete_hourly",
    )


def _count(returns: Sequence[PortfolioReturn]) -> int:
    return sum(item.missing_reason != MissingReason.INITIAL.value for item in returns)


def _undefined(
    name: str,
    reason: UndefinedReason,
    count: int,
    unit: str,
    coverage: str,
) -> MetricResult:
    return MetricResult(
        name=name,
        value=None,
        unit=unit,
        reason=reason,
        observation_count=count,
        coverage=coverage,
    )
