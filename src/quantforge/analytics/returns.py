"""Simple equity returns on a causal hourly clock.

A return exists only when two fresh marks are exactly one hour apart. Stale
valuations are not inputs. The first retained mark is an opening status, not a
return. The optional first mark may be the pre-trade anchor one hour before
evaluation entry. Earlier warm-up marks are not part of the sample.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from quantforge.analytics.models import (
    SHORT_SAMPLE_RETURNS,
    AnalyticsIntegrityError,
    PortfolioReturn,
    SampleCoverage,
    SegmentCoverage,
)
from quantforge.backtest.models import EquityPoint, PriceKind
from quantforge.research.models import MissingReason
from quantforge.research.returns import return_context, simple_return

_HOUR = timedelta(hours=1)


def performance_sample(
    points: Sequence[EquityPoint],
    entry_time: datetime,
    *,
    split_start: datetime | None = None,
) -> tuple[EquityPoint, ...]:
    """Return the pre-trade anchor and the marks at or after ``entry_time``.

    The anchor is eligible only when it is itself an in-evaluation mark exactly
    one hour before entry. A mark before ``split_start`` cannot be borrowed to
    start a validation return.
    """
    _require_equity_series(points)
    eligible = tuple(point for point in points if point.in_evaluation)
    anchor_time = entry_time - _HOUR
    if split_start is not None and anchor_time < split_start <= entry_time:
        raise AnalyticsIntegrityError("opening anchor would cross the evaluation split")
    anchors = tuple(point for point in eligible if point.time == anchor_time)
    body = tuple(point for point in eligible if point.time >= entry_time)
    if len(anchors) != 1 or not body or body[0].time != entry_time:
        raise AnalyticsIntegrityError(
            "evaluation entry has no eligible one-hour pre-trade anchor"
        )
    return (anchors[0], *body)


def equity_returns(
    points: Sequence[EquityPoint],
    *,
    opening_anchor: bool = False,
) -> tuple[PortfolioReturn, ...]:
    """Build simple returns without sorting, filling, or using stale marks."""
    _require_equity_series(points)
    if any(not point.in_evaluation for point in points):
        raise AnalyticsIntegrityError(
            "training marks cannot enter the performance return sample"
        )
    if not points:
        return ()
    first_role = "opening_anchor" if opening_anchor else "initial"
    results = [
        PortfolioReturn(
            time=points[0].time,
            previous_time=points[0].time,
            value=None,
            missing_reason=MissingReason.INITIAL.value,
            previous_role=first_role,
        )
    ]
    previous = points[0]
    for point in points[1:]:
        role = (
            "opening_anchor"
            if previous is points[0] and opening_anchor
            else "evaluation"
        )
        if point.time - previous.time != _HOUR:
            results.append(
                PortfolioReturn(
                    time=point.time,
                    previous_time=previous.time,
                    value=None,
                    missing_reason=MissingReason.GAP.value,
                    previous_role=role,
                )
            )
        else:
            results.append(
                PortfolioReturn(
                    time=point.time,
                    previous_time=previous.time,
                    value=simple_return(previous.equity, point.equity),
                    missing_reason=None,
                    previous_role=role,
                )
            )
        previous = point
    _assert_compound_identity(points, results)
    return tuple(results)


def sample_coverage(
    points: Sequence[EquityPoint],
    returns: Sequence[PortfolioReturn],
    *,
    evaluation_end: datetime,
) -> SampleCoverage:
    """Describe the sample without attaching a statistic to each segment."""
    valued = [item for item in returns if item.value is not None]
    gaps = [item for item in returns if item.missing_reason == MissingReason.GAP.value]
    complete = bool(points) and not gaps and len(valued) == max(len(points) - 1, 0)
    return SampleCoverage(
        equity_count=len(points),
        return_count=len(valued),
        gap_count=len(gaps),
        hourly_grid_complete=complete and len(valued) >= 1,
        opening_anchor_time=points[0].time,
        evaluation_start=points[1].time if len(points) > 1 else points[0].time,
        evaluation_end=evaluation_end,
        segments=_segments(returns),
        short_sample=len(valued) < SHORT_SAMPLE_RETURNS,
        open_mark_count=sum(point.price_kind is PriceKind.OPEN for point in points),
        close_mark_count=sum(point.price_kind is PriceKind.CLOSE for point in points),
    )


def _segments(returns: Sequence[PortfolioReturn]) -> tuple[SegmentCoverage, ...]:
    segments: list[SegmentCoverage] = []
    start: datetime | None = None
    end: datetime | None = None
    count = 0
    for item in returns:
        if item.value is None:
            if start is not None and end is not None and count:
                segments.append(SegmentCoverage(start, end, count))
            start = None
            end = None
            count = 0
            continue
        if start is None:
            start = item.previous_time
        end = item.time
        count += 1
    if start is not None and end is not None and count:
        segments.append(SegmentCoverage(start, end, count))
    return tuple(segments)


def _require_equity_series(points: Sequence[EquityPoint]) -> None:
    previous: EquityPoint | None = None
    for point in points:
        if not isinstance(point, EquityPoint):
            raise AnalyticsIntegrityError("equity input must be EquityPoint values")
        if point.price_kind not in (PriceKind.OPEN, PriceKind.CLOSE):
            raise AnalyticsIntegrityError("equity price kind must be open or close")
        if not point.fresh:
            raise AnalyticsIntegrityError("a stale mark cannot enter the equity sample")
        if point.price_open_time > point.time:
            raise AnalyticsIntegrityError(
                "valuation price is later than the mark timestamp"
            )
        if previous is not None and point.time <= previous.time:
            raise AnalyticsIntegrityError(
                "equity timestamps must be strictly increasing in the supplied order"
            )
        if previous is not None and point.price_open_time < previous.price_open_time:
            raise AnalyticsIntegrityError("valuation source timestamp moved backward")
        if not point.equity.is_finite() or point.equity <= 0:
            raise AnalyticsIntegrityError("equity must be finite and positive")
        if not point.price.is_finite() or point.price <= 0:
            raise AnalyticsIntegrityError("valuation price must be finite and positive")
        previous = point


def _assert_compound_identity(
    points: Sequence[EquityPoint],
    returns: Sequence[PortfolioReturn],
) -> None:
    if any(item.missing_reason == MissingReason.GAP.value for item in returns):
        return
    valued = [item.value for item in returns if item.value is not None]
    if len(points) < 2 or len(valued) != len(points) - 1:
        return
    with localcontext(return_context()):
        gross = Decimal(1)
        for value in valued:
            gross *= Decimal(1) + value
        endpoint = points[-1].equity / points[0].equity
        if abs(gross - endpoint) > abs(endpoint) * Decimal("1e-30"):
            raise AnalyticsIntegrityError(
                "hourly gross returns do not compound to the endpoint ratio"
            )
