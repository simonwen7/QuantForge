"""Tests for candle availability and chronological research splits."""

from datetime import UTC, datetime, timedelta

import pytest

from quantforge.research import (
    DEFAULT_PROTOCOL,
    HOLDOUT_END,
    HOLDOUT_START,
    TRAIN_START,
    VALIDATION_START,
    LookaheadError,
    ResearchError,
    SplitName,
    assert_feature_available,
    label_available_at,
    label_inside_split,
    observations_available_at,
    split_for_open_time,
    split_observations,
)
from tests.research.support import hourly_prices, price_observation


def test_close_time_is_not_treated_as_feature_availability():
    millisecond = price_observation(datetime(2024, 6, 1, 10, tzinfo=UTC), 100)
    microsecond = price_observation(datetime(2025, 6, 1, 10, tzinfo=UTC), 100)

    for observation in (millisecond, microsecond):
        assert observation.available_at == observation.open_time + timedelta(hours=1)
        assert observation.available_at != observation.close_time
        assert observation.close_time < observation.available_at
        with pytest.raises(LookaheadError):
            assert_feature_available(observation, observation.open_time)
        with pytest.raises(LookaheadError):
            assert_feature_available(observation, observation.close_time)
        with pytest.raises(LookaheadError):
            assert_feature_available(
                observation,
                observation.available_at - timedelta(microseconds=1),
            )
        assert_feature_available(observation, observation.available_at)

    assert millisecond.available_at - millisecond.close_time == timedelta(
        milliseconds=1
    )
    assert microsecond.available_at - microsecond.close_time == timedelta(
        microseconds=1
    )


def test_decision_before_hour_boundary_cannot_see_current_close():
    candle = price_observation(datetime(2024, 6, 1, 10, tzinfo=UTC), 100)

    hidden = observations_available_at(
        (candle,),
        datetime(2024, 6, 1, 10, 59, tzinfo=UTC),
    )
    visible = observations_available_at((candle,), candle.available_at)

    assert hidden == ()
    assert visible == (candle,)


def test_split_boundaries_are_half_open_and_chronological():
    last_train = datetime(2023, 12, 31, 23, tzinfo=UTC)
    first_validation = datetime(2024, 1, 1, tzinfo=UTC)
    last_validation = datetime(2024, 12, 31, 23, tzinfo=UTC)
    first_holdout = datetime(2025, 1, 1, tzinfo=UTC)
    last_holdout = datetime(2025, 12, 31, 23, tzinfo=UTC)

    assert split_for_open_time(last_train) is SplitName.TRAIN
    assert split_for_open_time(first_validation) is SplitName.VALIDATION
    assert split_for_open_time(last_validation) is SplitName.VALIDATION
    assert split_for_open_time(first_holdout) is SplitName.HOLDOUT
    assert split_for_open_time(last_holdout) is SplitName.HOLDOUT
    assert price_observation(last_train, 1).available_at == VALIDATION_START
    assert price_observation(last_validation, 1).available_at == HOLDOUT_START
    assert price_observation(last_holdout, 1).available_at == HOLDOUT_END
    assert TRAIN_START == datetime(2021, 1, 1, tzinfo=UTC)


def test_warmup_context_is_excluded_from_later_evaluation():
    observations = hourly_prices(
        datetime(2023, 12, 31, 22, tzinfo=UTC),
        (100, 101, 102, 103),
    )

    splits = split_observations(observations, warmup_observations=2)
    validation = splits.validation

    assert tuple(item.open_time for item in validation.context) == (
        datetime(2023, 12, 31, 22, tzinfo=UTC),
        datetime(2023, 12, 31, 23, tzinfo=UTC),
    )
    assert tuple(item.open_time for item in validation.evaluation) == (
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 1, tzinfo=UTC),
    )
    assert set(validation.context).isdisjoint(validation.evaluation)


def test_label_available_exactly_at_split_end_is_excluded():
    train_boundary = hourly_prices(
        datetime(2023, 12, 31, 21, tzinfo=UTC),
        (100, 101, 102, 103),
    )
    holdout_boundary = hourly_prices(
        datetime(2024, 12, 31, 21, tzinfo=UTC),
        (100, 101, 102, 103),
    )
    last_train = train_boundary[2]
    one_hour_before_last_train = train_boundary[1]

    horizon_one = split_observations(train_boundary, label_horizon_hours=1)
    horizon_zero = split_observations(train_boundary, label_horizon_hours=0)
    validation_horizon = split_observations(holdout_boundary, label_horizon_hours=1)

    assert label_available_at(one_hour_before_last_train, 1) == VALIDATION_START
    assert label_available_at(last_train, 0) == VALIDATION_START
    assert not label_inside_split(one_hour_before_last_train, DEFAULT_PROTOCOL.train, 1)
    assert not label_inside_split(last_train, DEFAULT_PROTOCOL.train, 0)
    assert tuple(item.open_time for item in horizon_one.train.label_eligible) == (
        datetime(2023, 12, 31, 21, tzinfo=UTC),
    )
    assert last_train in horizon_zero.train.evaluation
    assert last_train not in horizon_zero.train.label_eligible
    assert observations_available_at((last_train,), VALIDATION_START) == (last_train,)
    assert tuple(
        item.open_time for item in validation_horizon.validation.label_eligible
    ) == (datetime(2024, 12, 31, 21, tzinfo=UTC),)
    assert label_available_at(holdout_boundary[1], 1) == HOLDOUT_START
    assert holdout_boundary[1] not in validation_horizon.validation.label_eligible
    assert holdout_boundary[2] not in validation_horizon.validation.label_eligible


def test_missing_label_candle_is_not_eligible_and_splits_do_not_overlap():
    observations = (
        price_observation(datetime(2023, 12, 31, 21, tzinfo=UTC), 100),
        price_observation(datetime(2023, 12, 31, 23, tzinfo=UTC), 101),
        price_observation(datetime(2024, 1, 1, tzinfo=UTC), 102),
        price_observation(datetime(2024, 12, 31, 23, tzinfo=UTC), 103),
        price_observation(datetime(2025, 1, 1, tzinfo=UTC), 104),
        price_observation(datetime(2025, 12, 31, 23, tzinfo=UTC), 105),
    )

    splits = split_observations(
        observations,
        label_horizon_hours=1,
        warmup_observations=2,
    )
    evaluations = (
        splits.train.evaluation,
        splits.validation.evaluation,
        splits.holdout.evaluation,
    )

    assert label_inside_split(observations[0], DEFAULT_PROTOCOL.train, 1)
    assert splits.train.label_eligible == ()
    assert set(splits.train.evaluation).isdisjoint(splits.validation.evaluation)
    assert set(splits.validation.evaluation).isdisjoint(splits.holdout.evaluation)
    assert set(splits.train.label_eligible).isdisjoint(splits.validation.evaluation)
    assert set(splits.validation.label_eligible) <= set(splits.validation.evaluation)
    assert set(splits.validation.context).isdisjoint(splits.validation.evaluation)
    assert all(item.open_time < VALIDATION_START for item in splits.validation.context)
    for evaluation in evaluations:
        assert evaluation == tuple(sorted(evaluation, key=lambda item: item.open_time))
    with pytest.raises(ResearchError, match="chronological"):
        split_observations(tuple(reversed(observations)))
