"""Calendar contracts for the fixed training folds."""

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from quantforge.research.protocol import TRAIN_START, VALIDATION_START
from quantforge.research.walk_forward import (
    PRODUCTION_CALENDAR_ID,
    CalendarKind,
    WalkForwardCalendarError,
    WalkForwardFold,
    injected_calendar,
    production_calendar,
)

HOUR = timedelta(hours=1)


def test_production_folds_are_the_four_expanding_training_windows():
    calendar = production_calendar()
    folds = calendar.folds

    assert calendar.kind is CalendarKind.PRODUCTION
    assert calendar.calendar_id == PRODUCTION_CALENDAR_ID
    assert [fold.name for fold in folds] == ["fold-1", "fold-2", "fold-3", "fold-4"]
    assert [
        (
            fold.history_start,
            fold.history_end,
            fold.evaluation_start,
            fold.evaluation_end,
        )
        for fold in folds
    ] == [
        (
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2022, 1, 1, tzinfo=UTC),
            datetime(2022, 1, 1, tzinfo=UTC),
            datetime(2022, 7, 1, tzinfo=UTC),
        ),
        (
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2022, 7, 1, tzinfo=UTC),
            datetime(2022, 7, 1, tzinfo=UTC),
            datetime(2023, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2023, 1, 1, tzinfo=UTC),
            datetime(2023, 1, 1, tzinfo=UTC),
            datetime(2023, 7, 1, tzinfo=UTC),
        ),
        (
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2023, 7, 1, tzinfo=UTC),
            datetime(2023, 7, 1, tzinfo=UTC),
            datetime(2024, 1, 1, tzinfo=UTC),
        ),
    ]
    assert folds[0].history_start == TRAIN_START
    assert folds[-1].evaluation_end == VALIDATION_START
    year_end = datetime(2022, 12, 31, 23, tzinfo=UTC)
    validation_open = datetime(2024, 1, 1, tzinfo=UTC)
    for fold in folds:
        for bound in (
            fold.history_start,
            fold.history_end,
            fold.evaluation_start,
            fold.evaluation_end,
        ):
            assert bound.hour == 0
            assert bound.minute == 0
            assert bound.second == 0
        assert fold.history_end == fold.evaluation_start
    assert folds[1].evaluation_start <= year_end < folds[1].evaluation_end
    assert folds[1].evaluation_end == datetime(2023, 1, 1, tzinfo=UTC)
    assert all(fold.evaluation_end <= validation_open for fold in folds)
    assert not any(
        fold.evaluation_start <= validation_open < fold.evaluation_end for fold in folds
    )
    ends = [fold.evaluation_end for fold in folds[:-1]]
    starts = [fold.evaluation_start for fold in folds[1:]]
    assert ends == starts
    for previous, current in itertools.pairwise(folds):
        assert current.evaluation_start >= previous.evaluation_end
        assert current.history_end > previous.history_end
        assert current.history_start == previous.history_start


def test_injected_calendars_stay_labeled_and_inside_training():
    origin = datetime(2022, 6, 1, tzinfo=UTC)
    fold = WalkForwardFold(
        "short",
        origin,
        origin + 24 * HOUR,
        origin + 24 * HOUR,
        origin + 30 * HOUR,
    )
    calendar = injected_calendar("injected-synthetic-short-v1", (fold,))

    assert calendar.kind is CalendarKind.INJECTED
    assert calendar.calendar_id != PRODUCTION_CALENDAR_ID
    with pytest.raises(WalkForwardCalendarError, match="injected prefix"):
        injected_calendar(PRODUCTION_CALENDAR_ID, (fold,))
    with pytest.raises(WalkForwardCalendarError, match="shorter dates"):
        production_calendar_type = type(calendar)
        production_calendar_type(
            PRODUCTION_CALENDAR_ID,
            CalendarKind.PRODUCTION,
            (fold,),
        )


def test_overlap_rolling_history_and_validation_dates_are_rejected():
    origin = datetime(2022, 6, 1, tzinfo=UTC)
    first = WalkForwardFold(
        "a",
        origin,
        origin + 24 * HOUR,
        origin + 24 * HOUR,
        origin + 30 * HOUR,
    )
    overlap = WalkForwardFold(
        "b",
        origin,
        origin + 28 * HOUR,
        origin + 28 * HOUR,
        origin + 36 * HOUR,
    )
    with pytest.raises(WalkForwardCalendarError, match="overlap"):
        injected_calendar("injected-synthetic-overlap", (first, overlap))

    rolled = WalkForwardFold(
        "b",
        origin + 6 * HOUR,
        origin + 30 * HOUR,
        origin + 30 * HOUR,
        origin + 36 * HOUR,
    )
    with pytest.raises(WalkForwardCalendarError, match="expand"):
        injected_calendar("injected-synthetic-rolled", (first, rolled))

    with pytest.raises(WalkForwardCalendarError, match="training partition"):
        WalkForwardFold(
            "holdout",
            datetime(2024, 6, 1, tzinfo=UTC),
            datetime(2024, 7, 1, tzinfo=UTC),
            datetime(2024, 7, 1, tzinfo=UTC),
            datetime(2024, 8, 1, tzinfo=UTC),
        )
