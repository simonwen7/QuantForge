"""Candidate research-protocol document for a future authorized study.

The SHA-256 id covers research decisions only. It does not cover wall-clock
time, and it does not mean a person has approved the protocol. Empirical
access stays closed while human-approval acceptance is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantforge.analytics.models import (
    ANALYTICS_VERSION,
    ANNUALIZATION_PERIODS,
    BENCHMARK_ENTRY_POLICY,
    DEFAULT_SCENARIOS,
    MISSING_DATA_POLICY,
    RETURN_CONVENTION,
    RISK_FREE_RATE_HOURLY,
    SORTINO_DENOMINATOR,
    SORTINO_TARGET_HOURLY,
)
from quantforge.analytics.walk_forward_report import (
    SELECTION_POLICY,
    WALK_FORWARD_VERSION,
    baseline_outcome_count,
    sensitivity_outcome_count,
)
from quantforge.backtest.models import (
    DEFAULT_INITIAL_CASH,
    DEFAULT_QUANTUM,
    ENGINE_VERSION,
    LATENCY_BARS,
    VALUATION_CONVENTION,
    format_decimal,
)
from quantforge.research.experiment import experiment_id
from quantforge.research.models import AVAILABILITY_RULE, ResearchError
from quantforge.research.protocol import (
    HOLDOUT_END,
    HOLDOUT_START,
    TRAIN_START,
    VALIDATION_START,
)
from quantforge.strategies.mean_reversion import (
    BASELINE_ENTRY_THRESHOLD,
    BASELINE_LOOKBACK_HOURS,
    ENTRY_RULE,
    MEAN_REVERSION_DEFINITION,
    MEAN_REVERSION_FORMULA,
    MeanReversionConfig,
    mean_reversion_config_id,
)
from quantforge.strategies.models import (
    MOMENTUM_FORMULA,
    STRATEGY_DEFINITION,
    MomentumConfig,
)
from quantforge.strategies.momentum import strategy_config_id

PROTOCOL_CANDIDATE_VERSION = "m9-protocol-candidate-v1"
TERMINAL_MARK_POLICY = "boundary_close_at_exclusive_end_not_a_liquidation"
COVERAGE_REQUIREMENT = "strict_complete_hourly_partition"
FOLD_RESET_RULE = "each_evaluation_starts_at_10000_usdt_and_zero_btc"
EXECUTION_WINDOW_RULE = "fills_inside_half_open_window_only"
PUBLICATION_REQUIREMENT = (
    "no_raw_archive_or_price_redistribution_until_terms_are_accepted"
)
OBJECTIVE = (
    "Compare two pre-specified long-or-flat BTCUSDT spot rules with cash "
    "and buy-and-hold under three cost assumptions"
)
HYPOTHESES = (
    "A fixed 24-hour momentum rule and a fixed 24-hour relative-mean rule "
    "are evaluated without selecting a winner from their sensitivity grid"
)


class ProtocolFreezeError(ResearchError):
    """A protocol document violates the candidate-freeze contract."""


class ProtocolApprovalState(StrEnum):
    """Workflow label. This is not part of the research-decision hash."""

    DRAFT = "DRAFT"
    PENDING_HUMAN_APPROVAL = "PENDING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolFreezeError("protocol timestamps must be timezone-aware UTC")
    rendered = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if not rendered.endswith("Z"):
        raise ProtocolFreezeError("protocol timestamps must use UTC")
    return rendered


def candidate_protocol_document() -> dict[str, object]:
    """Return the canonical research decisions. Approval state is omitted."""
    momentum = MomentumConfig(lookback_hours=BASELINE_LOOKBACK_HOURS)
    mean_reversion = MeanReversionConfig(
        lookback_hours=BASELINE_LOOKBACK_HOURS,
        entry_threshold=BASELINE_ENTRY_THRESHOLD,
    )
    return {
        "analytics_version": ANALYTICS_VERSION,
        "annualization_periods": ANNUALIZATION_PERIODS,
        "availability_rule": AVAILABILITY_RULE,
        "benchmark_entry_policy": BENCHMARK_ENTRY_POLICY,
        "cash_policy": "no_orders",
        "coverage_requirement": COVERAGE_REQUIREMENT,
        "engine_version": ENGINE_VERSION,
        "execution_window_rule": EXECUTION_WINDOW_RULE,
        "fold_reset_rule": FOLD_RESET_RULE,
        "hypotheses": HYPOTHESES,
        "initial_cash": format_decimal(DEFAULT_INITIAL_CASH),
        "instrument": "BTCUSDT",
        "interval": "1h",
        "latency_bars": LATENCY_BARS,
        "market": "spot",
        "mean_reversion": {
            "config_id": mean_reversion_config_id(mean_reversion),
            "definition": MEAN_REVERSION_DEFINITION,
            "entry_rule": ENTRY_RULE,
            "formula": MEAN_REVERSION_FORMULA,
            "lookback_hours": mean_reversion.lookback_hours,
            "threshold": format_decimal(mean_reversion.entry_threshold),
        },
        "missing_data_policy": MISSING_DATA_POLICY,
        "momentum": {
            "config_id": strategy_config_id(momentum),
            "definition": STRATEGY_DEFINITION,
            "formula": MOMENTUM_FORMULA,
            "long_when": "momentum_strictly_positive",
            "lookback_hours": momentum.lookback_hours,
        },
        "objective": OBJECTIVE,
        "planned_outcomes": {
            "holdout_baseline": 4 * len(DEFAULT_SCENARIOS),
            "sensitivity_train_only": sensitivity_outcome_count(4),
            "train_walk_forward_baseline": baseline_outcome_count(4),
            "validation_baseline": 4 * len(DEFAULT_SCENARIOS),
        },
        "protocol_candidate_version": PROTOCOL_CANDIDATE_VERSION,
        "publication_requirement": PUBLICATION_REQUIREMENT,
        "quanta": {
            "btc": format_decimal(DEFAULT_QUANTUM),
            "cash": format_decimal(DEFAULT_QUANTUM),
        },
        "return_convention": RETURN_CONVENTION,
        "risk": {
            "drawdown": "observed_equity_over_running_peak_minus_one",
            "risk_free_rate_hourly": format_decimal(RISK_FREE_RATE_HOURLY),
            "sharpe": "mean_over_sample_std_times_sqrt_annualization",
            "sortino_denominator": SORTINO_DENOMINATOR,
            "sortino_target_hourly": format_decimal(SORTINO_TARGET_HOURLY),
            "undefined_when": "incomplete_hourly_grid_or_zero_dispersion",
        },
        "scenarios": [
            {
                "fee_rate": format_decimal(scenario.fee_rate),
                "name": scenario.name,
                "slippage_rate": format_decimal(scenario.slippage_rate),
            }
            for scenario in DEFAULT_SCENARIOS
        ],
        "selection_policy": SELECTION_POLICY,
        "splits": {
            "holdout": [_format_utc(HOLDOUT_START), _format_utc(HOLDOUT_END)],
            "train": [_format_utc(TRAIN_START), _format_utc(VALIDATION_START)],
            "validation": [_format_utc(VALIDATION_START), _format_utc(HOLDOUT_START)],
        },
        "terminal_mark_policy": TERMINAL_MARK_POLICY,
        "valuation_convention": VALUATION_CONVENTION,
        "walk_forward_version": WALK_FORWARD_VERSION,
    }


def protocol_document_id(document: dict[str, object]) -> str:
    """Hash research decisions. Callers must not insert a clock or approval."""
    if "approval_state" in document or "created_at" in document:
        raise ProtocolFreezeError(
            "protocol identity cannot include approval state or a timestamp"
        )
    return experiment_id(document)


@dataclass(frozen=True)
class ProtocolFreeze:
    """A candidate protocol. Construction does not approve it."""

    document: dict[str, object]
    protocol_id: str
    approval_state: ProtocolApprovalState = ProtocolApprovalState.PENDING_HUMAN_APPROVAL

    def __post_init__(self) -> None:
        if self.approval_state is ProtocolApprovalState.APPROVED:
            raise ProtocolFreezeError("constructing a protocol cannot mark it approved")
        if protocol_document_id(self.document) != self.protocol_id:
            raise ProtocolFreezeError("protocol id does not match its document")


def candidate_protocol_freeze() -> ProtocolFreeze:
    """Return the pending candidate. It is not an approved freeze."""
    document = candidate_protocol_document()
    return ProtocolFreeze(
        document=document,
        protocol_id=protocol_document_id(document),
        approval_state=ProtocolApprovalState.PENDING_HUMAN_APPROVAL,
    )


def draft_protocol_freeze() -> ProtocolFreeze:
    """Return the same decisions labeled as a draft."""
    candidate = candidate_protocol_freeze()
    return ProtocolFreeze(
        document=candidate.document,
        protocol_id=candidate.protocol_id,
        approval_state=ProtocolApprovalState.DRAFT,
    )


def assert_threshold_unchanged(value: Decimal) -> None:
    """Reject a caller that tries to retune the frozen threshold."""
    if value != BASELINE_ENTRY_THRESHOLD:
        raise ProtocolFreezeError("mean-reversion threshold is fixed at 0.02")
