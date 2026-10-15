"""Causal long-or-flat relative mean-reversion signals.

The baseline threshold is a pre-specified research convention. It is not an
optimized parameter, and a persistent decline can keep the rule long.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from quantforge.research.experiment import canonical_json
from quantforge.research.models import (
    RETURN_DECIMAL_PRECISION,
    HoldoutAccessError,
    PriceObservation,
    ResearchAccessMode,
    ResearchDataset,
)
from quantforge.research.returns import return_context, simple_return
from quantforge.strategies.models import (
    NEXT_BAR_OPEN,
    LookbackSource,
    NoSignalReason,
    SignalAction,
    StrategyConfigurationError,
    StrategyInputError,
)

MEAN_REVERSION_DEFINITION = "relative_mean_reversion_v1"
MEAN_REVERSION_FORMULA = "P_t / mean(P_(t-L), ..., P_(t-1)) - 1"
BASELINE_LOOKBACK_HOURS = 24
BASELINE_ENTRY_THRESHOLD = Decimal("0.02")
ENTRY_RULE = "long_if_deviation_strictly_below_negative_threshold"

_HOUR = timedelta(hours=1)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is not UTC:
        raise StrategyInputError("strategy timestamps must use datetime.UTC")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _format_threshold(value: Decimal) -> str:
    return format(value, "f")


@dataclass(frozen=True)
class MeanReversionConfig:
    """Fixed relative-mean baseline. ``entry_threshold`` is not fitted."""

    lookback_hours: int = BASELINE_LOOKBACK_HOURS
    entry_threshold: Decimal = BASELINE_ENTRY_THRESHOLD

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
        if isinstance(self.entry_threshold, bool) or not isinstance(
            self.entry_threshold, Decimal
        ):
            raise StrategyConfigurationError("entry_threshold must be a Decimal")
        if not self.entry_threshold.is_finite() or self.entry_threshold <= 0:
            raise StrategyConfigurationError(
                "entry_threshold must be finite and positive"
            )


def mean_reversion_config_payload(config: MeanReversionConfig) -> dict[str, object]:
    """Return the canonical definition. Wall-clock time is not included."""
    return {
        "decimal_precision": RETURN_DECIMAL_PRECISION,
        "entry_rule": ENTRY_RULE,
        "entry_threshold": _format_threshold(config.entry_threshold),
        "equality": "flat",
        "execution_policy": NEXT_BAR_OPEN,
        "formula": MEAN_REVERSION_FORMULA,
        "lookback_hours": config.lookback_hours,
        "position_scope": "long_or_flat",
        "strategy": MEAN_REVERSION_DEFINITION,
        "threshold_status": "pre_specified_convention_not_optimized",
    }


def mean_reversion_config_id(config: MeanReversionConfig) -> str:
    """Hash this definition without changing the M5 momentum identifier."""
    return hashlib.sha256(
        canonical_json(mean_reversion_config_payload(config)).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class MeanReversionSignal:
    """A causal mean-reversion state. ``deviation`` is not a momentum value."""

    open_time: datetime
    close_time: datetime
    available_at: datetime
    action: SignalAction
    deviation: Decimal | None
    no_signal_reason: NoSignalReason | None
    strategy_config_id: str
    lookback_hours: int
    entry_threshold: Decimal
    lookback_start: datetime
    lookback_end: datetime
    sources: tuple[LookbackSource, ...]
    execution_policy: str | None
    earliest_eligible_open: datetime | None

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC or self.close_time.tzinfo is not UTC:
            raise StrategyInputError("signal timestamps must use datetime.UTC")
        if self.available_at != self.open_time + _HOUR:
            raise StrategyInputError(
                "signal availability must be one hour after its decision open_time"
            )
        if self.lookback_end != self.open_time:
            raise StrategyInputError("lookback_end must be the decision candle open")
        if not self.strategy_config_id:
            raise StrategyInputError("strategy configuration identity is required")
        if isinstance(self.lookback_hours, bool) or self.lookback_hours < 1:
            raise StrategyInputError("signal lookback_hours must be a positive integer")
        if isinstance(self.entry_threshold, bool) or not isinstance(
            self.entry_threshold, Decimal
        ):
            raise StrategyInputError("entry_threshold must be a Decimal")
        if not self.entry_threshold.is_finite() or self.entry_threshold <= 0:
            raise StrategyInputError("entry_threshold must be finite and positive")
        _validate_sources(self)
        if self.action is SignalAction.NO_SIGNAL:
            _validate_abstention(self)
            return
        _validate_directional_signal(self)


def _validate_sources(signal: MeanReversionSignal) -> None:
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


def _validate_abstention(signal: MeanReversionSignal) -> None:
    if signal.deviation is not None or signal.no_signal_reason is None:
        raise StrategyInputError(
            "NO_SIGNAL must contain a reason and no deviation value"
        )
    if signal.execution_policy is not None or signal.earliest_eligible_open is not None:
        raise StrategyInputError("NO_SIGNAL must not expose an execution intent")
    if signal.no_signal_reason is NoSignalReason.WARMUP and (
        len(signal.sources) > signal.lookback_hours
    ):
        raise StrategyInputError("warmup cannot claim a complete lookback")


def _validate_directional_signal(signal: MeanReversionSignal) -> None:
    if signal.no_signal_reason is not None:
        raise StrategyInputError("a directional signal cannot also abstain")
    if signal.deviation is None or not signal.deviation.is_finite():
        raise StrategyInputError("directional deviation must be a finite Decimal")
    boundary = -signal.entry_threshold
    if signal.action is SignalAction.LONG and not signal.deviation < boundary:
        raise StrategyInputError(
            "LONG requires a deviation strictly below the threshold"
        )
    if signal.action is SignalAction.FLAT and not signal.deviation >= boundary:
        raise StrategyInputError("FLAT requires a deviation at or above the threshold")
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
        current.open_time == previous.open_time + _HOUR
        for previous, current in itertools.pairwise(signal.sources)
    )
    if not hourly:
        raise StrategyInputError("directional lookback must be an exact hourly chain")
    if signal.execution_policy != NEXT_BAR_OPEN:
        raise StrategyInputError("directional intent is NEXT_BAR_OPEN only")
    if signal.earliest_eligible_open != signal.available_at:
        raise StrategyInputError(
            "earliest eligible open must equal signal available_at"
        )


def _source(observation: PriceObservation) -> LookbackSource:
    return LookbackSource(
        open_time=observation.open_time,
        source_year=observation.source_year,
        source_month=observation.source_month,
        source_archive_sha256=observation.source_archive_sha256,
        source_schema_version=observation.source_schema_version,
    )


class MeanReversionStrategy:
    """Signal from the current close against the mean of the previous L closes.

    The decision close is excluded from the mean. The generator does not sort,
    fill, or read observations after the decision candle.
    """

    def __init__(self, config: MeanReversionConfig | None = None) -> None:
        self.config = config if config is not None else MeanReversionConfig()

    def generate(
        self,
        observations: Sequence[PriceObservation],
    ) -> tuple[MeanReversionSignal, ...]:
        """Return one signal per input candle, or raise before returning any."""
        self._require_chronological(observations)
        return tuple(
            self._signal_at(observations, index) for index in range(len(observations))
        )

    def generate_from_dataset(
        self, dataset: ResearchDataset
    ) -> tuple[MeanReversionSignal, ...]:
        """Signal a development dataset after rejecting holdout materialization."""
        self._reject_holdout_dataset(dataset)
        return self.generate(dataset.observations)

    def _signal_at(
        self,
        observations: Sequence[PriceObservation],
        index: int,
    ) -> MeanReversionSignal:
        decision = observations[index]
        lookback = self.config.lookback_hours
        if index < lookback:
            return self._signal(
                decision,
                observations[: index + 1],
                action=SignalAction.NO_SIGNAL,
                deviation=None,
                reason=NoSignalReason.WARMUP,
            )
        window = observations[index - lookback : index + 1]
        contiguous = all(
            current.open_time == previous.open_time + _HOUR
            for previous, current in itertools.pairwise(window)
        )
        if not contiguous:
            return self._signal(
                decision,
                window,
                action=SignalAction.NO_SIGNAL,
                deviation=None,
                reason=NoSignalReason.GAP,
            )
        deviation = _relative_deviation(window[:-1], window[-1].close)
        boundary = -self.config.entry_threshold
        action = SignalAction.LONG if deviation < boundary else SignalAction.FLAT
        return self._signal(
            decision,
            window,
            action=action,
            deviation=deviation,
            reason=None,
        )

    def _signal(
        self,
        decision: PriceObservation,
        inspected: Sequence[PriceObservation],
        *,
        action: SignalAction,
        deviation: Decimal | None,
        reason: NoSignalReason | None,
    ) -> MeanReversionSignal:
        directional = action is not SignalAction.NO_SIGNAL
        return MeanReversionSignal(
            open_time=decision.open_time,
            close_time=decision.close_time,
            available_at=decision.available_at,
            action=action,
            deviation=deviation,
            no_signal_reason=reason,
            strategy_config_id=mean_reversion_config_id(self.config),
            lookback_hours=self.config.lookback_hours,
            entry_threshold=self.config.entry_threshold,
            lookback_start=inspected[0].open_time,
            lookback_end=decision.open_time,
            sources=tuple(_source(observation) for observation in inspected),
            execution_policy=NEXT_BAR_OPEN if directional else None,
            earliest_eligible_open=decision.available_at if directional else None,
        )

    @staticmethod
    def _require_chronological(observations: Sequence[PriceObservation]) -> None:
        previous: datetime | None = None
        for observation in observations:
            if not isinstance(observation, PriceObservation):
                raise StrategyInputError(
                    "mean-reversion input must be PriceObservation values"
                )
            if previous is not None and observation.open_time <= previous:
                raise StrategyInputError(
                    "duplicate or reversed timestamps cannot produce partial signals"
                )
            previous = observation.open_time

    @staticmethod
    def _reject_holdout_dataset(dataset: ResearchDataset) -> None:
        if dataset.access_mode is ResearchAccessMode.FINAL_EVALUATION:
            raise HoldoutAccessError("M8 does not evaluate the final holdout")
        if dataset.access_mode is not ResearchAccessMode.DEVELOPMENT:
            raise StrategyInputError(
                f"unsupported research access mode: {dataset.access_mode!r}"
            )
        if dataset.final_evaluation_authorized:
            raise HoldoutAccessError(
                "development mode cannot authorize final-holdout access"
            )
        periods = tuple(dataset.covered_periods) + tuple(dataset.expected_periods)
        includes_holdout = any(period.year >= 2025 for period in periods)
        includes_holdout = includes_holdout or any(
            observation.source_year >= 2025 or observation.open_time.year >= 2025
            for observation in dataset.observations
        )
        if includes_holdout:
            raise HoldoutAccessError(
                "development mode cannot materialize final-holdout observations"
            )


def _relative_deviation(prior: Sequence[PriceObservation], current: Decimal) -> Decimal:
    """Return ``current / mean(prior) - 1`` at precision 50.

    ``prior`` is the previous L closes. ``current`` is excluded from the mean.
    """
    if not prior:
        raise StrategyInputError("the trailing mean requires previous closes")
    with localcontext(return_context()):
        total = sum((observation.close for observation in prior), Decimal(0))
        mean = total / Decimal(len(prior))
    return simple_return(mean, current)


def baseline_mean_reversion() -> MeanReversionStrategy:
    """Return the pre-specified 24-hour, 2% baseline."""
    return MeanReversionStrategy(
        MeanReversionConfig(
            lookback_hours=BASELINE_LOOKBACK_HOURS,
            entry_threshold=BASELINE_ENTRY_THRESHOLD,
        )
    )
