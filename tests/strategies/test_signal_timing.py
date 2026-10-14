"""Availability and causality tests for momentum signals."""

from datetime import UTC, datetime, timedelta

import pytest

from quantforge.research import (
    DEFAULT_PROTOCOL,
    VALIDATION_START,
    LookaheadError,
    SplitName,
    assert_feature_available,
    label_inside_split,
    observations_available_at,
    split_for_open_time,
)
from quantforge.strategies import (
    BASELINE_LOOKBACK_HOURS,
    MomentumConfig,
    MomentumStrategy,
    NoSignalReason,
    SignalAction,
)
from tests.research.support import hourly_prices, price_observation

HOUR = timedelta(hours=1)


@pytest.mark.parametrize("year", [2024, 2025])
def test_signal_is_unavailable_until_the_following_hour(year: int):
    start = datetime(year, 6, 1, 10, tzinfo=UTC)
    signal = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(
        hourly_prices(start, (100, 110))
    )[-1]
    tick = timedelta(milliseconds=1) if year < 2025 else timedelta(microseconds=1)

    assert signal.available_at == signal.open_time + HOUR
    assert signal.available_at - signal.close_time == tick
    assert signal.execution_policy == "NEXT_BAR_OPEN"
    assert signal.earliest_eligible_open == signal.available_at
    for decision_time in (
        signal.open_time,
        signal.close_time,
        signal.available_at - timedelta(microseconds=1),
    ):
        with pytest.raises(LookaheadError):
            assert_feature_available(signal, decision_time)
        assert observations_available_at((signal,), decision_time) == ()
    assert_feature_available(signal, signal.available_at)
    assert observations_available_at((signal,), signal.available_at) == (signal,)


def test_future_close_does_not_change_an_earlier_signal():
    start = datetime(2024, 6, 1, tzinfo=UTC)
    observations = hourly_prices(start, (100, 110, 90, 80))
    changed = (
        observations[0],
        observations[1],
        observations[2],
        price_observation(observations[3].open_time, 500),
    )
    strategy = MomentumStrategy(MomentumConfig(lookback_hours=1))
    original = strategy.generate(observations)
    updated = strategy.generate(changed)

    assert original[:-1] == updated[:-1]
    assert original[-1] != updated[-1]
    assert original[1].sources[-1].open_time < changed[-1].open_time


def test_final_training_signal_is_not_a_training_label():
    start = datetime(2023, 12, 30, 23, tzinfo=UTC)
    closes = tuple(100 + offset for offset in range(BASELINE_LOOKBACK_HOURS + 1))
    observations = hourly_prices(start, closes)
    signal = MomentumStrategy().generate(observations)[-1]

    assert signal.open_time == datetime(2023, 12, 31, 23, tzinfo=UTC)
    assert split_for_open_time(signal.open_time) is SplitName.TRAIN
    assert signal.available_at == VALIDATION_START
    assert signal.earliest_eligible_open == VALIDATION_START
    assert not label_inside_split(observations[-1], DEFAULT_PROTOCOL.train, 0)


def test_no_signal_has_no_execution_intent():
    signal = MomentumStrategy().generate(
        hourly_prices(
            datetime(2024, 6, 1, tzinfo=UTC),
            (100,),
        )
    )[0]

    assert signal.action is SignalAction.NO_SIGNAL
    assert signal.no_signal_reason is NoSignalReason.WARMUP
    assert signal.execution_policy is None
    assert signal.earliest_eligible_open is None
    assert signal.momentum is None
