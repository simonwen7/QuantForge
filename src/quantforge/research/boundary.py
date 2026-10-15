"""Partition-boundary clock and the exclusive-end close mark.

Membership stays on candle open time. Fills stay inside the half-open M6
window. The completion of the last in-partition candle is a valuation at the
exclusive end, using that candle's close. It is not a liquidation and it does
not require a bar from the following year.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from quantforge.backtest.models import EquityPoint, PriceKind
from quantforge.research.market_bars import DataOrigin, MarketBar
from quantforge.research.models import ResearchError, candle_available_at
from quantforge.research.protocol import (
    HOLDOUT_END,
    HOLDOUT_START,
    TRAIN_START,
    VALIDATION_START,
)

_HOUR = timedelta(hours=1)


class BoundaryError(ResearchError):
    """A boundary clock or terminal mark violates the partition contract."""


@dataclass(frozen=True)
class PartitionClock:
    """Decision, fill, and terminal-mark times for one half-open partition."""

    name: str
    start: datetime
    end: datetime
    first_decision_open: datetime
    signal_available_at: datetime
    earliest_fill_at: datetime
    anchor_at: datetime
    last_open: datetime
    last_close_time: datetime
    terminal_mark_at: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is not UTC or self.end.tzinfo is not UTC:
            raise BoundaryError("partition bounds must use datetime.UTC")
        if self.end != self.start + (self.end - self.start):
            raise BoundaryError("partition bounds are inconsistent")
        if self.first_decision_open != self.start:
            raise BoundaryError("the first decision opens at the partition start")
        if self.signal_available_at != self.start + _HOUR:
            raise BoundaryError(
                "the first signal is available one hour after the start"
            )
        if self.earliest_fill_at != self.start + 2 * _HOUR:
            raise BoundaryError("the earliest fill is two hours after the start")
        if self.anchor_at != self.signal_available_at:
            raise BoundaryError("the pre-trade anchor is the signal-availability mark")
        if self.last_open != self.end - _HOUR:
            raise BoundaryError(
                "the last candle opens one hour before the exclusive end"
            )
        if self.terminal_mark_at != self.end:
            raise BoundaryError("the terminal mark is timed at the exclusive end")
        if self.terminal_mark_at != candle_available_at(self.last_open):
            raise BoundaryError("the terminal mark is the last candle's availability")
        if not self.last_open < self.last_close_time < self.terminal_mark_at:
            raise BoundaryError("the terminal close falls inside the last open hour")


def _close_tick(open_time: datetime) -> timedelta:
    if open_time.year < 2025:
        return timedelta(milliseconds=1)
    return timedelta(microseconds=1)


def partition_clock(name: str, start: datetime, end: datetime) -> PartitionClock:
    """Build the approved clock without loading a market bar."""
    if start.tzinfo is not UTC or end.tzinfo is not UTC:
        raise BoundaryError("partition bounds must use datetime.UTC")
    if start >= end:
        raise BoundaryError("partition bounds must be half-open and nonempty")
    last_open = end - _HOUR
    return PartitionClock(
        name=name,
        start=start,
        end=end,
        first_decision_open=start,
        signal_available_at=start + _HOUR,
        earliest_fill_at=start + 2 * _HOUR,
        anchor_at=start + _HOUR,
        last_open=last_open,
        last_close_time=last_open + _HOUR - _close_tick(last_open),
        terminal_mark_at=end,
    )


def approved_partition_clocks() -> tuple[PartitionClock, ...]:
    """Return the train, validation, and holdout clocks."""
    return (
        partition_clock("train", TRAIN_START, VALIDATION_START),
        partition_clock("validation", VALIDATION_START, HOLDOUT_START),
        partition_clock("holdout", HOLDOUT_START, HOLDOUT_END),
    )


def fill_due_exactly_at_end(clock: PartitionClock) -> datetime:
    """Return the decision open whose latency-one fill would land on ``end``."""
    return clock.end - 2 * _HOUR


def assert_no_following_bar(bars: Sequence[MarketBar], clock: PartitionClock) -> None:
    """Reject a bar that opens at or after the exclusive end."""
    for bar in bars:
        if bar.origin is not DataOrigin.SYNTHETIC_UNVERIFIED:
            raise BoundaryError("boundary fixtures must stay synthetic")
        if bar.open_time >= clock.end:
            raise BoundaryError(
                "a following-period bar cannot create the terminal mark"
            )
        if bar.open_time.year > clock.last_open.year:
            raise BoundaryError("a following-year bar cannot create the terminal mark")


def select_terminal_mark(
    points: Sequence[EquityPoint],
    clock: PartitionClock,
    source_bar: MarketBar,
) -> EquityPoint:
    """Select the exclusive-end close mark. This function does not trade.

    The mark must already exist, must be outside fill eligibility, and must
    cite the last in-partition candle. Cash and BTC are left as the engine
    recorded them.
    """
    if source_bar.open_time != clock.last_open:
        raise BoundaryError("terminal source bar is not the last in-partition open")
    if source_bar.close_time != clock.last_close_time:
        raise BoundaryError("terminal source close_time does not match the convention")
    if source_bar.available_at != clock.terminal_mark_at:
        raise BoundaryError("terminal source availability is not the exclusive end")
    matched = [point for point in points if point.time == clock.terminal_mark_at]
    if len(matched) != 1:
        raise BoundaryError("the exclusive end needs exactly one equity mark")
    point = matched[0]
    if point.in_evaluation:
        raise BoundaryError("the terminal mark is not an in-window fill timestamp")
    if point.price_kind is not PriceKind.CLOSE:
        raise BoundaryError("the terminal mark uses the completed close")
    if point.price_open_time != source_bar.open_time or point.price != source_bar.close:
        raise BoundaryError("the terminal mark does not cite the last candle")
    previous = [
        earlier
        for earlier in points
        if earlier.time == clock.last_open and earlier.price_kind is PriceKind.OPEN
    ]
    if len(previous) != 1:
        raise BoundaryError("the last open mark is required before the terminal close")
    if point.cash != previous[0].cash or point.btc != previous[0].btc:
        raise BoundaryError("terminal valuation must not change cash or BTC")
    return point
