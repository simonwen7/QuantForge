"""Immutable time-series momentum signals and configuration."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from quantforge.research.models import ResearchError, candle_available_at

STRATEGY_DEFINITION = "time_series_momentum_v1"
BASELINE_LOOKBACK_HOURS = 24
NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
MOMENTUM_FORMULA = "P_t / P_(t-lookback_hours) - 1"


class StrategyError(ResearchError):
    """Base class for expected momentum-strategy failures."""


class StrategyInputError(StrategyError):
    """Strategy observations are duplicated, reversed, or the wrong type."""


class StrategyConfigurationError(StrategyError):
    """A strategy configuration violates the M5 contract."""


class SignalAction(StrEnum):
    """Desired directional state. This is not an order."""

    LONG = "long"
    FLAT = "flat"
    NO_SIGNAL = "no_signal"


class NoSignalReason(StrEnum):
    """Why a candle does not produce a directional state."""

    WARMUP = "warmup"
    GAP = "gap"


@dataclass(frozen=True)
class MomentumConfig:
    """Fixed momentum baseline parameters.

    ``lookback_hours`` counts consecutive one-hour close-to-close steps. The
    default of 24 therefore uses 25 closes. M5 does not search this value.
    """

    lookback_hours: int = BASELINE_LOOKBACK_HOURS

    def __post_init__(self) -> None:
        if isinstance(self.lookback_hours, bool) or not isinstance(
            self.lookback_hours, int
        ):
            raise StrategyConfigurationError(
                "lookback_hours must be a positive integer"
            )
        if self.lookback_hours < 1:
            raise StrategyConfigurationError(
                "lookback_hours must be a positive integer"
            )


@dataclass(frozen=True)
class LookbackSource:
    """Source identity of one close that was actually inspected."""

    open_time: datetime
    source_year: int
    source_month: int
    source_archive_sha256: str
    source_schema_version: str

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC:
            raise StrategyInputError("lookback source timestamps must use datetime.UTC")


@dataclass(frozen=True)
class StrategySignal:
    """A causal momentum state aligned to one decision candle.

    ``LONG`` and ``FLAT`` name the desired market exposure. They do not carry
    a quantity, fee, or fill. ``NO_SIGNAL`` is neither flat nor a sell, and it
    exposes no execution intent. ``NEXT_BAR_OPEN`` records only the earliest
    hourly open at which a later engine may attempt execution.
    """

    open_time: datetime
    close_time: datetime
    available_at: datetime
    action: SignalAction
    momentum: Decimal | None
    no_signal_reason: NoSignalReason | None
    strategy_config_id: str
    lookback_hours: int
    lookback_start: datetime
    lookback_end: datetime
    sources: tuple[LookbackSource, ...]
    execution_policy: str | None
    earliest_eligible_open: datetime | None

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC or self.close_time.tzinfo is not UTC:
            raise StrategyInputError("signal timestamps must use datetime.UTC")
        if self.available_at != candle_available_at(self.open_time):
            raise StrategyInputError(
                "signal availability must be one hour after its decision open_time"
            )
        if self.lookback_end != self.open_time:
            raise StrategyInputError("lookback_end must be the decision candle open")
        if not self.strategy_config_id:
            raise StrategyInputError("strategy configuration identity is required")
        if isinstance(self.lookback_hours, bool) or self.lookback_hours < 1:
            raise StrategyInputError("signal lookback_hours must be a positive integer")
        _validate_sources(self)
        if self.action is SignalAction.NO_SIGNAL:
            _validate_abstention(self)
            return
        _validate_directional_signal(self)


def _validate_sources(signal: StrategySignal) -> None:
    if not signal.sources:
        raise StrategyInputError("a signal must record the closes it inspected")
    if signal.sources[0].open_time != signal.lookback_start:
        raise StrategyInputError("lookback_start must be the earliest inspected open")
    if signal.sources[-1].open_time != signal.open_time:
        raise StrategyInputError("lineage must end at the decision candle")
    previous = signal.sources[0].open_time
    for source in signal.sources[1:]:
        if source.open_time <= previous:
            raise StrategyInputError("lookback lineage must be strictly chronological")
        previous = source.open_time


def _validate_abstention(signal: StrategySignal) -> None:
    if signal.momentum is not None or signal.no_signal_reason is None:
        raise StrategyInputError(
            "NO_SIGNAL must contain a reason and no momentum value"
        )
    if signal.execution_policy is not None or signal.earliest_eligible_open is not None:
        raise StrategyInputError("NO_SIGNAL must not expose an execution intent")
    if signal.no_signal_reason is NoSignalReason.WARMUP and (
        len(signal.sources) > signal.lookback_hours
    ):
        raise StrategyInputError("warmup cannot claim a complete lookback")


def _validate_directional_signal(signal: StrategySignal) -> None:
    if signal.no_signal_reason is not None:
        raise StrategyInputError("a directional signal cannot also abstain")
    if signal.momentum is None or not signal.momentum.is_finite():
        raise StrategyInputError("directional momentum must be a finite Decimal")
    if signal.action is SignalAction.LONG and not signal.momentum > 0:
        raise StrategyInputError("LONG requires positive momentum")
    if signal.action is SignalAction.FLAT and not signal.momentum <= 0:
        raise StrategyInputError("FLAT requires nonpositive momentum")
    if len(signal.sources) != signal.lookback_hours + 1:
        raise StrategyInputError(
            "a directional signal requires lookback_hours + 1 closes"
        )
    expected_start = signal.open_time - timedelta(hours=signal.lookback_hours)
    if signal.lookback_start != expected_start:
        raise StrategyInputError(
            "directional lookback does not span the configured hours"
        )
    hourly = all(
        current.open_time == previous.open_time + timedelta(hours=1)
        for previous, current in itertools.pairwise(signal.sources)
    )
    if not hourly:
        raise StrategyInputError("directional lookback must be an exact hourly chain")
    if signal.execution_policy != NEXT_BAR_OPEN:
        raise StrategyInputError("M5 directional intent is NEXT_BAR_OPEN only")
    if signal.earliest_eligible_open != signal.available_at:
        raise StrategyInputError(
            "earliest eligible open must equal signal available_at"
        )
