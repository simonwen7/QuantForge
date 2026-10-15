"""Causal checks for the fixed relative-mean rule."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from quantforge.research.models import PriceObservation
from quantforge.research.returns import return_context
from quantforge.strategies import strategy_config_id
from quantforge.strategies.mean_reversion import (
    BASELINE_ENTRY_THRESHOLD,
    MeanReversionConfig,
    MeanReversionSignal,
    MeanReversionStrategy,
    baseline_mean_reversion,
    mean_reversion_config_id,
)
from quantforge.strategies.models import (
    NEXT_BAR_OPEN,
    LookbackSource,
    SignalAction,
    StrategyConfigurationError,
    StrategyInputError,
)
from quantforge.strategies.momentum import MomentumConfig

START = datetime(2022, 1, 31, tzinfo=UTC)
HOUR = timedelta(hours=1)
TICK = timedelta(milliseconds=1)


def _observation(
    open_time: datetime, close: str, sha: str | None = None
) -> PriceObservation:
    return PriceObservation(
        open_time=open_time,
        close_time=open_time + HOUR - TICK,
        close=Decimal(close),
        source_archive_sha256=(sha or "a" * 64),
        source_year=open_time.year,
        source_month=open_time.month,
        source_schema_version="synthetic-unverified",
    )


def _series(
    closes: list[str], *, start: datetime = START
) -> tuple[PriceObservation, ...]:
    return tuple(
        _observation(start + index * HOUR, close, sha=f"{index % 10}" * 64)
        for index, close in enumerate(closes)
    )


def _flat_then(current: str) -> tuple[MeanReversionSignal, ...]:
    closes = ["100"] * 24 + [current]
    return MeanReversionStrategy().generate(_series(closes))


def _oracle(current: str, priors: list[str] | None = None) -> tuple[str, Decimal]:
    """Classify from the prior closes only. This does not call the strategy."""
    values = [Decimal(price) for price in (priors or ["100"] * 24)]
    with localcontext(return_context()):
        mean = sum(values, Decimal(0)) / Decimal(len(values))
        deviation = Decimal(current) / mean - Decimal(1)
        contaminated = (sum(values, Decimal(0)) + Decimal(current)) / Decimal(
            len(values) + 1
        )
        contaminated_deviation = Decimal(current) / contaminated - Decimal(1)
    assert deviation != contaminated_deviation
    action = "long" if deviation < Decimal("-0.02") else "flat"
    return action, deviation


def test_threshold_boundaries_use_the_previous_closes_only():
    expected = {
        "97.99": _oracle("97.99"),
        "98": _oracle("98"),
        "98.01": _oracle("98.01"),
        "110": _oracle("110"),
    }
    long_signal = _flat_then("97.99")[-1]
    exact = _flat_then("98")[-1]
    above = _flat_then("98.01")[-1]
    richer = _flat_then("110")[-1]

    assert long_signal.action.value == expected["97.99"][0]
    assert long_signal.deviation == expected["97.99"][1]
    assert exact.action.value == expected["98"][0]
    assert exact.deviation == expected["98"][1]
    assert above.action.value == expected["98.01"][0]
    assert above.deviation == expected["98.01"][1]
    assert richer.action.value == expected["110"][0]
    assert richer.deviation == expected["110"][1]
    assert len(long_signal.sources) == 25
    assert long_signal.execution_policy == NEXT_BAR_OPEN
    assert long_signal.earliest_eligible_open == long_signal.available_at
    assert long_signal.available_at == long_signal.open_time + HOUR
    assert long_signal.entry_threshold == BASELINE_ENTRY_THRESHOLD


def test_unequal_priors_exclude_the_decision_close_from_the_mean():
    priors = [str(100 + index) for index in range(24)]
    action, deviation = _oracle("50", priors)
    signal = MeanReversionStrategy().generate(_series([*priors, "50"]))[-1]

    assert signal.action.value == action
    assert signal.deviation == deviation
    assert len(signal.sources) == 25


def test_a_nonfinite_close_is_rejected_before_a_signal():
    with pytest.raises(Exception, match="finite"):
        _observation(START, "NaN")


def test_deviation_matches_an_independent_precision_50_ratio():
    signal = MeanReversionStrategy().generate(_series(["3"] * 24 + ["1"]))[-1]
    with localcontext(return_context()):
        expected = Decimal(-2) / Decimal(3)

    assert signal.action is SignalAction.LONG
    assert signal.deviation == expected
    assert signal.deviation != Decimal(1 / 3 - 1)


def test_warmup_precedes_gap_and_a_later_hourly_chain_recovers():
    short = MeanReversionStrategy().generate(_series(["100", "100", "90"]))
    assert all(signal.action is SignalAction.NO_SIGNAL for signal in short)
    assert all(signal.no_signal_reason.value == "warmup" for signal in short)
    assert short[-1].execution_policy is None
    assert short[-1].deviation is None

    broken_times = [START + index * HOUR for index in range(24)]
    broken_times.append(START + 25 * HOUR)
    broken = MeanReversionStrategy().generate(
        tuple(_observation(instant, "100") for instant in broken_times)
    )
    assert broken[-1].no_signal_reason.value == "gap"
    assert len(broken[-1].sources) == 25

    recovered_start = START + 25 * HOUR
    recovered = MeanReversionStrategy().generate(
        tuple(
            _observation(recovered_start + index * HOUR, "100") for index in range(25)
        )
    )
    assert recovered[-1].action is SignalAction.FLAT
    assert recovered[-1].deviation == Decimal(0)


def test_invalid_observations_and_configuration_raise_before_a_signal():
    series = _series(["100"] * 3)
    with pytest.raises(StrategyInputError, match="duplicate or reversed"):
        MeanReversionStrategy().generate((series[0], series[0]))
    with pytest.raises(StrategyInputError, match="PriceObservation"):
        MeanReversionStrategy().generate(("100",))  # type: ignore[arg-type]
    with pytest.raises(StrategyConfigurationError, match="positive integer"):
        MeanReversionConfig(lookback_hours=0)
    with pytest.raises(StrategyConfigurationError, match="Decimal"):
        MeanReversionConfig(entry_threshold=0.02)  # type: ignore[arg-type]
    prior = START - HOUR
    with pytest.raises(StrategyInputError, match="strictly below"):
        MeanReversionSignal(
            open_time=START,
            close_time=START + HOUR - TICK,
            available_at=START + HOUR,
            action=SignalAction.LONG,
            deviation=Decimal("-0.01"),
            no_signal_reason=None,
            strategy_config_id="x" * 8,
            lookback_hours=1,
            entry_threshold=Decimal("0.02"),
            lookback_start=prior,
            lookback_end=START,
            sources=(
                LookbackSource(prior, 2022, 1, "b" * 64, "synthetic-unverified"),
                LookbackSource(START, 2022, 1, "c" * 64, "synthetic-unverified"),
            ),
            execution_policy=NEXT_BAR_OPEN,
            earliest_eligible_open=START + HOUR,
        )


def test_lineage_crosses_a_month_and_future_closes_do_not_rewrite_history():
    closes = ["100"] * 24 + ["97"]
    original = _series(closes)
    signals = MeanReversionStrategy().generate(original)
    decision = signals[-1]
    months = {source.source_month for source in decision.sources}
    archives = {source.source_archive_sha256 for source in decision.sources}

    assert decision.action is SignalAction.LONG
    assert months == {1, 2}
    assert len(archives) > 1
    assert len(decision.sources) == 25

    changed = original[:-1] + (_observation(original[-1].open_time, "110"),)
    rerun = MeanReversionStrategy().generate(changed)
    assert signals[:-1] == rerun[:-1]
    assert rerun[-1].action is SignalAction.FLAT


def test_configuration_identity_is_stable_and_distinct_from_momentum():
    first = mean_reversion_config_id(MeanReversionConfig())
    second = mean_reversion_config_id(baseline_mean_reversion().config)
    other = mean_reversion_config_id(
        MeanReversionConfig(entry_threshold=Decimal("0.05"))
    )

    assert first == second
    assert first != other
    assert first != strategy_config_id(MomentumConfig())
    assert MeanReversionStrategy().generate(_series(["100"] * 25)) == (
        MeanReversionStrategy().generate(_series(["100"] * 25))
    )
