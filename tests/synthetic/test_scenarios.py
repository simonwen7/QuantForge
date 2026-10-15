"""Synthetic clock invariants."""

import itertools

from quantforge.research.market_bars import MarketBar
from quantforge.synthetic.scenarios import (
    GENERATOR_VERSION,
    generate_scenario,
    scenario_identity,
)


def test_the_same_seed_rebuilds_the_same_bars_and_identity():
    first = generate_scenario("uptrend", 7, 48)
    second = generate_scenario("uptrend", 7, 48)
    assert first == second
    assert scenario_identity("uptrend", 7, 48) == scenario_identity("uptrend", 7, 48)
    assert scenario_identity("uptrend", 7, 48) != scenario_identity("uptrend", 8, 48)
    assert first[0].origin.value == "synthetic_unverified"
    assert first[0].open_time.year == 2022
    assert first[0].source_schema_version == "synthetic-unverified"
    assert GENERATOR_VERSION in "m11-synthetic-scenario-v1"


def test_candles_are_valid_and_scenarios_differ():
    up = generate_scenario("uptrend", 3, 64)
    down = generate_scenario("downtrend", 3, 64)
    assert [bar.close for bar in up] != [bar.close for bar in down]
    for bar in (*up, *generate_scenario("shock", 3, 64)):
        assert isinstance(bar, MarketBar)
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.volume > 0
        assert bar.open_time.year == 2022


def test_the_gap_scenario_omits_one_hour():
    bars = generate_scenario("gap", 4, 48)
    deltas = [
        (right.open_time - left.open_time).total_seconds()
        for left, right in itertools.pairwise(bars)
    ]
    assert deltas.count(7200) == 1
    assert all(delta in {3600, 7200} for delta in deltas)
