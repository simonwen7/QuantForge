"""Fixed chronological research splits and causal feature availability."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from quantforge.research.models import (
    LookaheadError,
    PriceObservation,
    ResearchError,
    SplitName,
    candle_available_at,
)

TRAIN_START = datetime(2021, 1, 1, tzinfo=UTC)
VALIDATION_START = datetime(2024, 1, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 1, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class SplitWindow:
    """A half-open UTC interval, ``[start, end)``."""

    name: SplitName
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is not UTC or self.end.tzinfo is not UTC:
            raise ResearchError("split boundaries must use datetime.UTC")
        if self.start >= self.end:
            raise ResearchError("split boundaries must be half-open and nonempty")

    def contains(self, open_time: datetime) -> bool:
        """Return whether a candle open belongs to this split."""
        return self.start <= open_time < self.end


@dataclass(frozen=True)
class ResearchProtocol:
    """The approved train, validation, and final-holdout boundaries."""

    train: SplitWindow
    validation: SplitWindow
    holdout: SplitWindow

    def __post_init__(self) -> None:
        expected = (
            (SplitName.TRAIN, TRAIN_START, VALIDATION_START),
            (SplitName.VALIDATION, VALIDATION_START, HOLDOUT_START),
            (SplitName.HOLDOUT, HOLDOUT_START, HOLDOUT_END),
        )
        actual = (
            (self.train.name, self.train.start, self.train.end),
            (self.validation.name, self.validation.start, self.validation.end),
            (self.holdout.name, self.holdout.start, self.holdout.end),
        )
        if actual != expected:
            raise ResearchError("M4 only permits the approved chronological protocol")

    def window_for(self, name: SplitName) -> SplitWindow:
        """Return one approved split window."""
        return {
            SplitName.TRAIN: self.train,
            SplitName.VALIDATION: self.validation,
            SplitName.HOLDOUT: self.holdout,
        }[name]


DEFAULT_PROTOCOL = ResearchProtocol(
    train=SplitWindow(SplitName.TRAIN, TRAIN_START, VALIDATION_START),
    validation=SplitWindow(SplitName.VALIDATION, VALIDATION_START, HOLDOUT_START),
    holdout=SplitWindow(SplitName.HOLDOUT, HOLDOUT_START, HOLDOUT_END),
)


def split_for_open_time(
    open_time: datetime,
    protocol: ResearchProtocol = DEFAULT_PROTOCOL,
) -> SplitName:
    """Classify a candle by its open time."""
    if open_time.tzinfo is not UTC:
        raise ResearchError("classification requires a UTC open_time")
    for name in SplitName:
        if protocol.window_for(name).contains(open_time):
            return name
    raise ResearchError("observation open_time is outside the approved protocol")


def label_available_at(observation: PriceObservation, horizon_hours: int) -> datetime:
    """Return when a future close label becomes conservatively observable."""
    if horizon_hours < 0:
        raise ResearchError("label horizon cannot be negative")
    return candle_available_at(observation.open_time + timedelta(hours=horizon_hours))


def label_inside_split(
    observation: PriceObservation,
    window: SplitWindow,
    horizon_hours: int,
) -> bool:
    """Whether a close label is observable strictly before the split ends.

    The label is the close of the candle that opens ``horizon_hours`` after the
    feature candle, including the feature candle itself when the horizon is
    zero. That close becomes observable at the labeled candle's ``available_at``,
    which is one hour after its open. An availability time equal to the
    exclusive split end belongs to the next split and is not eligible. This
    time rule does not by itself prove that the labeled candle was present.
    """
    if horizon_hours < 0:
        raise ResearchError("label horizon cannot be negative")
    if not window.contains(observation.open_time):
        return False
    return label_available_at(observation, horizon_hours) < window.end


class HasAvailability(Protocol):
    """A close-derived value with an explicit research availability time."""

    open_time: datetime
    available_at: datetime


def assert_feature_available(
    observation: HasAvailability,
    decision_time: datetime,
) -> None:
    """Reject use of a close-derived value before its candle has completed."""
    if decision_time.tzinfo is not UTC:
        raise LookaheadError("decision_time must use datetime.UTC")
    if decision_time < observation.available_at:
        raise LookaheadError(
            "close-derived data for "
            f"{observation.open_time.isoformat()} is unavailable until "
            f"{observation.available_at.isoformat()}"
        )


def observations_available_at[T: HasAvailability](
    observations: Sequence[T],
    decision_time: datetime,
) -> tuple[T, ...]:
    """Return only values whose candles are completed at or before a decision."""
    if decision_time.tzinfo is not UTC:
        raise LookaheadError("decision_time must use datetime.UTC")
    return tuple(
        observation
        for observation in observations
        if observation.available_at <= decision_time
    )


@dataclass(frozen=True)
class SplitSelection:
    """Evaluation rows for one split and prior context excluded from its metrics."""

    name: SplitName
    start: datetime
    end: datetime
    context: tuple[PriceObservation, ...]
    evaluation: tuple[PriceObservation, ...]
    label_eligible: tuple[PriceObservation, ...]


@dataclass(frozen=True)
class DatasetSplits:
    """All three protocol selections for one chronological dataset."""

    train: SplitSelection
    validation: SplitSelection
    holdout: SplitSelection


def split_observations(
    observations: Sequence[PriceObservation],
    *,
    protocol: ResearchProtocol = DEFAULT_PROTOCOL,
    label_horizon_hours: int = 0,
    warmup_observations: int = 0,
) -> DatasetSplits:
    """Split observations without shuffling or crossing label boundaries.

    Warm-up context may precede a split, but those rows are neither evaluation
    rows nor supervised labels. A label is eligible only when its labeled
    candle is present in this sequence and that candle's close is observable
    strictly before the split ends. Availability exactly at the exclusive end
    is excluded. M4 provides no fitting routine; validation and holdout rows
    are not added to the training label set.
    """
    if label_horizon_hours < 0 or warmup_observations < 0:
        raise ResearchError("horizon and warm-up counts cannot be negative")
    previous: datetime | None = None
    for observation in observations:
        if previous is not None and observation.open_time <= previous:
            raise ResearchError("split inputs must be strictly chronological")
        split_for_open_time(observation.open_time, protocol)
        previous = observation.open_time

    present_at = {observation.open_time for observation in observations}

    def select(window: SplitWindow) -> SplitSelection:
        prior = tuple(
            observation
            for observation in observations
            if observation.open_time < window.start
        )
        context = prior[-warmup_observations:] if warmup_observations else ()
        evaluation = tuple(
            observation
            for observation in observations
            if window.contains(observation.open_time)
        )
        eligible = tuple(
            observation
            for observation in evaluation
            if label_inside_split(observation, window, label_horizon_hours)
            and observation.open_time + timedelta(hours=label_horizon_hours)
            in present_at
        )
        return SplitSelection(
            name=window.name,
            start=window.start,
            end=window.end,
            context=context,
            evaluation=evaluation,
            label_eligible=eligible,
        )

    return DatasetSplits(
        train=select(protocol.train),
        validation=select(protocol.validation),
        holdout=select(protocol.holdout),
    )
