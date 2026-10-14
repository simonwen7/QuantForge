"""Formula, lineage, and configuration tests for time-series momentum."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from quantforge.research import ResearchDataError
from quantforge.strategies import (
    BASELINE_LOOKBACK_HOURS,
    MomentumConfig,
    MomentumStrategy,
    NoSignalReason,
    SignalAction,
    StrategyConfigurationError,
    StrategyInputError,
    StrategySignal,
    serialize_lookback_lineage,
    strategy_config_id,
)
from tests.research.support import hourly_prices, price_observation

HOUR = timedelta(hours=1)
START = datetime(2024, 6, 1, tzinfo=UTC)


def test_default_rule_requires_twenty_five_closes_and_matches_fraction():
    closes = tuple(100 + offset for offset in range(BASELINE_LOOKBACK_HOURS + 1))
    signals = MomentumStrategy().generate(hourly_prices(START, closes))
    decision = signals[-1]
    expected = Fraction(closes[-1], closes[0]) - 1

    assert len(closes) == 25
    assert all(
        signal.action is SignalAction.NO_SIGNAL
        and signal.no_signal_reason is NoSignalReason.WARMUP
        for signal in signals[:-1]
    )
    assert decision.action is SignalAction.LONG
    assert decision.momentum == Decimal("0.24")
    assert type(decision.momentum) is Decimal
    assert abs(Fraction(decision.momentum) - expected) < Fraction(1, 10**40)
    assert len(decision.sources) == 25
    assert decision.lookback_start == START
    assert decision.lookback_end == START + 24 * HOUR


def test_sign_rules_use_one_endpoint_ratio_not_chained_hourly_returns():
    strategy = MomentumStrategy(MomentumConfig(lookback_hours=2))
    positive = strategy.generate(hourly_prices(START, (100, 999, 110)))
    negative = strategy.generate(hourly_prices(START, (100, 50, 90)))
    zero = strategy.generate(hourly_prices(START, (100, 125, 100)))

    assert positive[-1].action is SignalAction.LONG
    assert positive[-1].momentum == Decimal("0.1")
    assert negative[-1].action is SignalAction.FLAT
    assert negative[-1].momentum == Decimal("-0.1")
    assert zero[-1].action is SignalAction.FLAT
    assert zero[-1].momentum == Decimal(0)
    chained = (Decimal(999) / Decimal(100) - 1) + (Decimal(110) / Decimal(999) - 1)
    assert positive[-1].momentum != chained


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        (Decimal(100), Decimal(110)),
        (Decimal(3), Decimal(1)),
        (Decimal(50), Decimal(50)),
    ],
)
def test_momentum_matches_exact_fraction(previous: Decimal, current: Decimal):
    signal = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(
        hourly_prices(START, (previous, current))
    )[-1]
    expected = Fraction(current) / Fraction(previous) - 1

    assert type(signal.momentum) is Decimal
    assert abs(Fraction(signal.momentum) - expected) < Fraction(1, 10**40)


def test_warmup_gap_and_recovery_do_not_bridge_missing_hours():
    observations = (
        price_observation(START, 100),
        price_observation(START + 2 * HOUR, 110),
        price_observation(START + 3 * HOUR, 120),
    )
    signals = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(observations)

    assert signals[0].no_signal_reason is NoSignalReason.WARMUP
    assert signals[0].momentum is None
    assert len(signals[0].sources) == 1
    assert signals[1].no_signal_reason is NoSignalReason.GAP
    assert signals[1].momentum is None
    assert tuple(source.open_time for source in signals[1].sources) == (
        observations[0].open_time,
        observations[1].open_time,
    )
    assert signals[2].action is SignalAction.LONG
    assert signals[2].momentum is not None
    assert abs(Fraction(signals[2].momentum) - (Fraction(120, 110) - 1)) < Fraction(
        1, 10**40
    )


def test_duplicate_and_reversed_timestamps_raise_without_a_result():
    first = price_observation(START, 100)
    second = price_observation(START + HOUR, 110)
    duplicate = (first, price_observation(START, 120))

    with pytest.raises(StrategyInputError, match="duplicate or reversed"):
        MomentumStrategy().generate((second, first))
    with pytest.raises(StrategyInputError, match="duplicate or reversed"):
        MomentumStrategy().generate(duplicate)


def test_invalid_prices_and_configuration_are_rejected():
    with pytest.raises(ResearchDataError):
        price_observation(START, 0)
    with pytest.raises(ResearchDataError):
        price_observation(START, -1)
    for lookback in (0, -2, True, False, 1.5):
        with pytest.raises(StrategyConfigurationError):
            MomentumConfig(lookback_hours=lookback)  # type: ignore[arg-type]


def test_configuration_identity_is_deterministic_and_has_no_search_api():
    baseline = MomentumConfig()
    same = MomentumConfig(lookback_hours=24)
    other = MomentumConfig(lookback_hours=12)

    assert strategy_config_id(baseline) == strategy_config_id(same)
    assert strategy_config_id(baseline) != strategy_config_id(other)
    assert not hasattr(MomentumStrategy, "fit")
    assert not hasattr(MomentumStrategy, "optimize")
    assert "quantity" not in StrategySignal.__dataclass_fields__
    assert "fill_price" not in StrategySignal.__dataclass_fields__
    assert "fee" not in StrategySignal.__dataclass_fields__


def test_cross_month_lineage_records_every_inspected_close():
    first = price_observation(
        datetime(2023, 12, 31, 23, tzinfo=UTC),
        100,
        archive_sha="a" * 64,
    )
    second = price_observation(
        datetime(2024, 1, 1, tzinfo=UTC),
        105,
        archive_sha="b" * 64,
    )
    decision = price_observation(
        datetime(2024, 1, 1, 1, tzinfo=UTC),
        110,
        archive_sha="c" * 64,
    )
    changed_history = price_observation(
        first.open_time,
        first.close,
        archive_sha="d" * 64,
    )
    strategy = MomentumStrategy(MomentumConfig(lookback_hours=2))
    original = strategy.generate((first, second, decision))[-1]
    changed = strategy.generate((changed_history, second, decision))[-1]
    serialized = serialize_lookback_lineage(original.sources)

    assert [
        (source.source_year, source.source_month) for source in original.sources
    ] == [
        (2023, 12),
        (2024, 1),
        (2024, 1),
    ]
    assert original.sources[-1].source_archive_sha256 == "c" * 64
    assert (
        changed.sources[-1].source_archive_sha256
        == original.sources[-1].source_archive_sha256
    )
    assert (
        changed.sources[0].source_archive_sha256
        != original.sources[0].source_archive_sha256
    )
    assert serialized == serialize_lookback_lineage(original.sources)
    assert serialized.index("a" * 64) < serialized.index("c" * 64)
    assert serialize_lookback_lineage(changed.sources) != serialized


def test_empty_input_returns_no_signals_and_no_future_bar_is_created():
    assert MomentumStrategy().generate(()) == ()
    observations = hourly_prices(START, (100, 110))
    signals = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(observations)

    assert len(signals) == len(observations)
    assert signals[-1].open_time == observations[-1].open_time
    assert START + 2 * HOUR not in {signal.open_time for signal in signals}


def test_warmup_precedes_gap_until_a_full_window_can_be_rebuilt():
    # 23 hourly bars, then a two-hour jump, then 24 hourly bars.
    prefix = [START + offset * HOUR for offset in range(23)]
    restarted = prefix[-1] + 2 * HOUR
    rebuilt = [restarted + offset * HOUR for offset in range(25)]
    times = prefix + rebuilt
    missing = prefix[-1] + HOUR
    observations = tuple(
        price_observation(open_time, 100 + offset)
        for offset, open_time in enumerate(times)
    )
    signals = MomentumStrategy().generate(observations)
    first_valid = signals[23 + 24]

    assert len(observations) == 23 + 25
    assert all(
        signal.no_signal_reason is NoSignalReason.WARMUP for signal in signals[:24]
    )
    assert signals[23].no_signal_reason is NoSignalReason.WARMUP
    assert missing not in {source.open_time for source in signals[23].sources}
    assert all(
        signal.no_signal_reason is NoSignalReason.GAP for signal in signals[24:47]
    )
    assert missing not in {source.open_time for source in signals[24].sources}
    assert len(signals[24].sources) == 25
    assert first_valid.action is SignalAction.LONG
    assert tuple(source.open_time for source in first_valid.sources) == tuple(rebuilt)
    assert signals[46].no_signal_reason is NoSignalReason.GAP
    with pytest.raises(AttributeError):
        signals[23].sources[0].source_archive_sha256 = "e" * 64  # type: ignore[misc]


def test_cross_month_baseline_records_all_twenty_five_source_identities():
    start = datetime(2023, 12, 31, tzinfo=UTC)
    observations = tuple(
        price_observation(
            start + offset * HOUR,
            200 + offset,
            archive_sha=("a" if offset < 24 else "b") * 64,
        )
        for offset in range(25)
    )
    changed = (
        price_observation(
            observations[0].open_time, observations[0].close, archive_sha="d" * 64
        ),
        *observations[1:],
    )
    signal = MomentumStrategy().generate(observations)[-1]
    changed_signal = MomentumStrategy().generate(changed)[-1]
    serialized = serialize_lookback_lineage(signal.sources)

    assert signal.action is SignalAction.LONG
    assert len(signal.sources) == 25
    assert [source.source_month for source in signal.sources[:24]] == [12] * 24
    assert signal.sources[-1].source_month == 1
    assert signal.sources[-1].source_year == 2024
    assert {source.source_archive_sha256 for source in signal.sources[:24]} == {
        "a" * 64
    }
    assert signal.sources[-1].source_archive_sha256 == "b" * 64
    assert {source.source_schema_version for source in signal.sources} == {
        "binance-spot-kline-12-v1"
    }
    assert changed_signal.sources[-1] == signal.sources[-1]
    assert serialize_lookback_lineage(changed_signal.sources) != serialized
    assert serialized == serialize_lookback_lineage(signal.sources)
    assert observations[-1].open_time not in {
        source.open_time
        for source in MomentumStrategy().generate(observations[:-1])[-1].sources
    }


def test_no_signal_is_not_a_directional_action():
    signal = MomentumStrategy().generate(hourly_prices(START, (100,)))[0]

    assert set(SignalAction) == {
        SignalAction.LONG,
        SignalAction.FLAT,
        SignalAction.NO_SIGNAL,
    }
    assert signal.action is SignalAction.NO_SIGNAL
    assert signal.action is not SignalAction.FLAT
    assert signal.action is not SignalAction.LONG


def test_repeated_generation_is_identical():
    observations = hourly_prices(START, (100, 101, 102, 103))
    strategy = MomentumStrategy(MomentumConfig(lookback_hours=1))

    assert strategy.generate(observations) == strategy.generate(list(observations))
