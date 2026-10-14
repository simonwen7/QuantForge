"""Causal research signals. M5 does not create orders or fills."""

from quantforge.strategies.models import (
    BASELINE_LOOKBACK_HOURS,
    MOMENTUM_FORMULA,
    NEXT_BAR_OPEN,
    STRATEGY_DEFINITION,
    LookbackSource,
    MomentumConfig,
    NoSignalReason,
    SignalAction,
    StrategyConfigurationError,
    StrategyError,
    StrategyInputError,
    StrategySignal,
)
from quantforge.strategies.momentum import (
    MomentumStrategy,
    baseline_strategy,
    serialize_lookback_lineage,
    strategy_config_id,
    strategy_config_payload,
)

__all__ = [
    "BASELINE_LOOKBACK_HOURS",
    "MOMENTUM_FORMULA",
    "NEXT_BAR_OPEN",
    "STRATEGY_DEFINITION",
    "LookbackSource",
    "MomentumConfig",
    "MomentumStrategy",
    "NoSignalReason",
    "SignalAction",
    "StrategyConfigurationError",
    "StrategyError",
    "StrategyInputError",
    "StrategySignal",
    "baseline_strategy",
    "serialize_lookback_lineage",
    "strategy_config_id",
    "strategy_config_payload",
]
