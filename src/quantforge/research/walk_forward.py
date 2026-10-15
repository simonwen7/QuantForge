"""Fixed-rule chronological folds inside the training partition.

This calendar does not fit parameters. An injected calendar is a synthetic
test fixture and is not the production semiannual study.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from quantforge.research.models import ResearchError
from quantforge.research.protocol import TRAIN_START, VALIDATION_START

PRODUCTION_CALENDAR_ID = "m8-train-semiannual-v1"
INJECTED_CALENDAR_PREFIX = "injected-synthetic-"


class WalkForwardCalendarError(ResearchError):
    """A fold calendar violates the training-period contract."""


class CalendarKind(StrEnum):
    """Whether the dates are the production study or an injected fixture."""

    PRODUCTION = "production_train_semiannual"
    INJECTED = "injected_synthetic"


def _hour(value: datetime, label: str) -> None:
    if value.tzinfo is not UTC:
        raise WalkForwardCalendarError(f"{label} must use datetime.UTC")
    if (value.minute, value.second, value.microsecond) != (0, 0, 0):
        raise WalkForwardCalendarError(f"{label} must be an exact UTC hour")


@dataclass(frozen=True)
class WalkForwardFold:
    """One expanding-history fold. Evaluation is half-open and inside training."""

    name: str
    history_start: datetime
    history_end: datetime
    evaluation_start: datetime
    evaluation_end: datetime

    def __post_init__(self) -> None:
        if not self.name:
            raise WalkForwardCalendarError("a fold name is required")
        _hour(self.history_start, "history_start")
        _hour(self.history_end, "history_end")
        _hour(self.evaluation_start, "evaluation_start")
        _hour(self.evaluation_end, "evaluation_end")
        if self.history_end != self.evaluation_start:
            raise WalkForwardCalendarError(
                "history_end must equal the evaluation start"
            )
        if not self.history_start < self.history_end < self.evaluation_end:
            raise WalkForwardCalendarError(
                "fold boundaries must be strictly increasing"
            )
        if self.history_start < TRAIN_START or self.evaluation_end > VALIDATION_START:
            raise WalkForwardCalendarError(
                "folds must stay inside the training partition"
            )


@dataclass(frozen=True)
class WalkForwardCalendar:
    """An ordered expanding calendar. Production dates are exact."""

    calendar_id: str
    kind: CalendarKind
    folds: tuple[WalkForwardFold, ...]

    def __post_init__(self) -> None:
        validate_calendar(self)


def _production_folds() -> tuple[WalkForwardFold, ...]:
    def at(year: int, month: int, day: int) -> datetime:
        return datetime(year, month, day, tzinfo=UTC)

    origin = at(2021, 1, 1)
    return (
        WalkForwardFold(
            "fold-1", origin, at(2022, 1, 1), at(2022, 1, 1), at(2022, 7, 1)
        ),
        WalkForwardFold(
            "fold-2", origin, at(2022, 7, 1), at(2022, 7, 1), at(2023, 1, 1)
        ),
        WalkForwardFold(
            "fold-3", origin, at(2023, 1, 1), at(2023, 1, 1), at(2023, 7, 1)
        ),
        WalkForwardFold(
            "fold-4", origin, at(2023, 7, 1), at(2023, 7, 1), at(2024, 1, 1)
        ),
    )


def production_calendar() -> WalkForwardCalendar:
    """Return the four frozen training folds. This does not load market data."""
    return WalkForwardCalendar(
        calendar_id=PRODUCTION_CALENDAR_ID,
        kind=CalendarKind.PRODUCTION,
        folds=_production_folds(),
    )


def injected_calendar(
    calendar_id: str,
    folds: tuple[WalkForwardFold, ...],
) -> WalkForwardCalendar:
    """Return a synthetic fixture calendar. It is not the semiannual study."""
    if not calendar_id.startswith(INJECTED_CALENDAR_PREFIX):
        raise WalkForwardCalendarError(
            "an injected calendar id must start with the injected prefix"
        )
    if calendar_id == PRODUCTION_CALENDAR_ID:
        raise WalkForwardCalendarError(
            "an injected calendar cannot use the production id"
        )
    return WalkForwardCalendar(
        calendar_id=calendar_id,
        kind=CalendarKind.INJECTED,
        folds=folds,
    )


def validate_calendar(calendar: WalkForwardCalendar) -> None:
    """Reject overlap, rolling history, and a mislabeled production calendar."""
    if not calendar.calendar_id:
        raise WalkForwardCalendarError("a calendar id is required")
    if not calendar.folds:
        raise WalkForwardCalendarError("a calendar requires at least one fold")
    names = [fold.name for fold in calendar.folds]
    if len(names) != len(set(names)):
        raise WalkForwardCalendarError("fold names must be unique")
    origin = calendar.folds[0].history_start
    previous_end: datetime | None = None
    previous_history_end: datetime | None = None
    for fold in calendar.folds:
        if fold.history_start != origin:
            raise WalkForwardCalendarError(
                "history must expand from one origin rather than roll forward"
            )
        if (
            previous_history_end is not None
            and fold.history_end <= previous_history_end
        ):
            raise WalkForwardCalendarError("expanding history must grow at each fold")
        if previous_end is not None and fold.evaluation_start < previous_end:
            raise WalkForwardCalendarError("evaluation windows must not overlap")
        previous_end = fold.evaluation_end
        previous_history_end = fold.history_end
    if calendar.kind is CalendarKind.PRODUCTION:
        if calendar.calendar_id != PRODUCTION_CALENDAR_ID:
            raise WalkForwardCalendarError("production calendar id is fixed")
        if calendar.folds != _production_folds():
            raise WalkForwardCalendarError(
                "production folds cannot be replaced with shorter dates"
            )
        return
    if calendar.kind is not CalendarKind.INJECTED:
        raise WalkForwardCalendarError(f"unsupported calendar kind: {calendar.kind!r}")
    if calendar.calendar_id == PRODUCTION_CALENDAR_ID:
        raise WalkForwardCalendarError(
            "an injected calendar cannot use the production id"
        )
    if not calendar.calendar_id.startswith(INJECTED_CALENDAR_PREFIX):
        raise WalkForwardCalendarError(
            "an injected calendar id must start with the injected prefix"
        )
    if calendar.folds == _production_folds():
        raise WalkForwardCalendarError(
            "the production dates must use the production calendar kind"
        )
