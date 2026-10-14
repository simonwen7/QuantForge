"""Causal long-or-flat time-series momentum signals."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.research.experiment import canonical_json
from quantforge.research.models import (
    RETURN_DECIMAL_PRECISION,
    HoldoutAccessError,
    PriceObservation,
    ResearchAccessMode,
    ResearchDataset,
)
from quantforge.research.returns import simple_return
from quantforge.strategies.models import (
    BASELINE_LOOKBACK_HOURS,
    MOMENTUM_FORMULA,
    NEXT_BAR_OPEN,
    STRATEGY_DEFINITION,
    LookbackSource,
    MomentumConfig,
    NoSignalReason,
    SignalAction,
    StrategyInputError,
    StrategySignal,
)

_HOUR = timedelta(hours=1)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is not UTC:
        raise StrategyInputError("strategy timestamps must use datetime.UTC")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def strategy_config_payload(config: MomentumConfig) -> dict[str, object]:
    """Return the canonical strategy definition, excluding any clock time."""
    return {
        "decimal_precision": RETURN_DECIMAL_PRECISION,
        "execution_note": (
            "NEXT_BAR_OPEN is an idealized zero-latency intent, not a fill"
        ),
        "execution_policy": NEXT_BAR_OPEN,
        "formula": MOMENTUM_FORMULA,
        "lookback_hours": config.lookback_hours,
        "position_scope": "long_or_flat",
        "strategy": STRATEGY_DEFINITION,
    }


def strategy_config_id(config: MomentumConfig) -> str:
    """Hash the strategy definition without changing M4 experiment identifiers."""
    return hashlib.sha256(
        canonical_json(strategy_config_payload(config)).encode("utf-8")
    ).hexdigest()


def serialize_lookback_lineage(sources: Sequence[LookbackSource]) -> str:
    """Serialize inspected closes in chronological order."""
    payload = {
        "sources": [
            {
                "open_time": _format_utc(source.open_time),
                "source_archive_sha256": source.source_archive_sha256,
                "source_month": source.source_month,
                "source_schema_version": source.source_schema_version,
                "source_year": source.source_year,
            }
            for source in sources
        ]
    }
    return canonical_json(payload)


def _source(observation: PriceObservation) -> LookbackSource:
    return LookbackSource(
        open_time=observation.open_time,
        source_year=observation.source_year,
        source_month=observation.source_month,
        source_archive_sha256=observation.source_archive_sha256,
        source_schema_version=observation.source_schema_version,
    )


class MomentumStrategy:
    """Generate causal momentum states from verified hourly closes.

    The calculation is one precision-50 ratio of the newest and oldest closes
    in a complete hourly chain. It is not a sum or product of rounded one-hour
    returns. The generator does not sort, fill, or inspect observations after
    the decision candle.
    """

    def __init__(self, config: MomentumConfig | None = None) -> None:
        self.config = config if config is not None else MomentumConfig()

    def generate(
        self,
        observations: Sequence[PriceObservation],
    ) -> tuple[StrategySignal, ...]:
        """Return one signal per input candle, or raise before returning any."""
        self._require_chronological(observations)
        return tuple(
            self._signal_at(observations, index) for index in range(len(observations))
        )

    def generate_from_dataset(
        self, dataset: ResearchDataset
    ) -> tuple[StrategySignal, ...]:
        """Signal a development dataset after rejecting holdout materialization.

        M5 does not implement final-holdout evaluation. Directly constructed
        synthetic timestamps remain available through :meth:`generate`.
        """
        self._reject_holdout_dataset(dataset)
        return self.generate(dataset.observations)

    def _signal_at(
        self,
        observations: Sequence[PriceObservation],
        index: int,
    ) -> StrategySignal:
        decision = observations[index]
        lookback = self.config.lookback_hours
        # WARMUP wins while fewer than lookback_hours preceding observations
        # exist, even when that short prefix is discontinuous. GAP applies
        # only to a full trailing window that is not an exact hourly chain.
        if index < lookback:
            context = observations[: index + 1]
            return self._signal(
                decision,
                context,
                action=SignalAction.NO_SIGNAL,
                momentum=None,
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
                momentum=None,
                reason=NoSignalReason.GAP,
            )
        momentum = simple_return(window[0].close, window[-1].close)
        action = SignalAction.LONG if momentum > 0 else SignalAction.FLAT
        return self._signal(
            decision,
            window,
            action=action,
            momentum=momentum,
            reason=None,
        )

    def _signal(
        self,
        decision: PriceObservation,
        inspected: Sequence[PriceObservation],
        *,
        action: SignalAction,
        momentum: Decimal | None,
        reason: NoSignalReason | None,
    ) -> StrategySignal:
        directional = action is not SignalAction.NO_SIGNAL
        return StrategySignal(
            open_time=decision.open_time,
            close_time=decision.close_time,
            available_at=decision.available_at,
            action=action,
            momentum=momentum,
            no_signal_reason=reason,
            strategy_config_id=strategy_config_id(self.config),
            lookback_hours=self.config.lookback_hours,
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
                    "momentum input must be PriceObservation values"
                )
            if previous is not None and observation.open_time <= previous:
                raise StrategyInputError(
                    "duplicate or reversed timestamps cannot produce partial signals"
                )
            previous = observation.open_time

    @staticmethod
    def _reject_holdout_dataset(dataset: ResearchDataset) -> None:
        if dataset.access_mode is ResearchAccessMode.FINAL_EVALUATION:
            raise HoldoutAccessError("M5 does not evaluate the final holdout")
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


def baseline_strategy() -> MomentumStrategy:
    """Return the pre-specified 24-hour baseline."""
    return MomentumStrategy(MomentumConfig(lookback_hours=BASELINE_LOOKBACK_HOURS))
