"""Integer-clock synthetic OHLCV. The same inputs rebuild the same bars."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.research.experiment import experiment_id
from quantforge.research.market_bars import MarketBar, synthetic_market_bar
from quantforge.research.models import ResearchError

GENERATOR_VERSION = "m11-synthetic-scenario-v1"
SCENARIO_NAMES = ("uptrend", "downtrend", "sideways", "shock", "gap")
MIN_BARS = 48
MAX_BARS = 168
COMPARE_MAX_BARS = 120
MIN_SEED = 0
MAX_SEED = 10_000
MAX_LOOKBACK = 48
CLOCK_START = datetime(2022, 6, 1, tzinfo=UTC)
_ORIGIN_CENTS = 3_000_000
_HOUR = timedelta(hours=1)
_WAVE = (-8, -6, -3, -1, 2, 5, 7, 8, 6, 3, 0, -2, -5, -7, -6, -4, -1)


class ScenarioError(ResearchError):
    """A synthetic request is outside the published bounds."""


def scenario_identity(name: str, seed: int, bar_count: int) -> str:
    """Hash the generator contract. The wall clock is not an input."""
    return experiment_id(
        {
            "bar_count": bar_count,
            "clock": "2022-06-01T00:00:00.000000Z",
            "generator_version": GENERATOR_VERSION,
            "scenario": name,
            "seed": seed,
        }
    )


def generate_scenario(name: str, seed: int, bar_count: int) -> tuple[MarketBar, ...]:
    """Return hourly June 2022 bars for one named scenario."""
    _validate(name, seed, bar_count)
    gap = name == "gap"
    gap_at = bar_count // 2
    bars: list[MarketBar] = []
    previous_close = _ORIGIN_CENTS
    for index in range(bar_count):
        shift = 1 if gap and index >= gap_at else 0
        open_cents = previous_close
        close_cents = _bounded(open_cents + _delta(name, index, seed))
        wick = abs(_WAVE[(index + seed) % len(_WAVE)]) * 15 + 10
        high = max(open_cents, close_cents) + wick
        low = min(open_cents, close_cents) - wick
        low = max(low, 100)
        volume = 500 + (seed * 17 + index * 13) % 800
        bars.append(
            synthetic_market_bar(
                CLOCK_START + _HOUR * (index + shift),
                _money(open_cents),
                high=_money(high),
                low=_money(low),
                close=_money(close_cents),
                volume=Decimal(volume),
                archive_sha=scenario_identity(name, seed, bar_count),
            )
        )
        previous_close = close_cents
    return tuple(bars)


def _validate(name: str, seed: int, bar_count: int) -> None:
    if name not in SCENARIO_NAMES:
        raise ScenarioError(f"unsupported scenario: {name}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError("seed must be an integer")
    if seed < MIN_SEED or seed > MAX_SEED:
        raise ScenarioError("seed must be between 0 and 10000")
    if isinstance(bar_count, bool) or not isinstance(bar_count, int):
        raise ScenarioError("bar_count must be an integer")
    if bar_count < MIN_BARS or bar_count > MAX_BARS:
        raise ScenarioError("bar_count must be between 48 and 168")


def _delta(name: str, index: int, seed: int) -> int:
    wave = _WAVE[(index * 3 + seed) % len(_WAVE)]
    if name == "uptrend":
        return 80 + wave * 40
    if name == "downtrend":
        return -80 + wave * 40
    if name == "shock" and 36 <= index < 48:
        return wave * 400 - 250
    if name == "shock":
        return wave * 50
    return wave * 120


def _bounded(cents: int) -> int:
    if cents < 50_000:
        return 50_000
    if cents > 20_000_000:
        return 20_000_000
    return cents


def _money(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)
