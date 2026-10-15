"""Report objects for the synthetic M7 research path.

Metric floats are binary64 estimates. Monetary amounts and drawdowns stay
``Decimal``. A present value and a present undefined reason cannot coexist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantforge.backtest.models import VALUATION_CONVENTION
from quantforge.research.models import ResearchError

ANALYTICS_VERSION = "m7-analytics-v1"
MISSING_DATA_POLICY = "complete_hourly_grid_or_undefined_dispersion"
RETURN_CONVENTION = "hourly_asof_equity_mark"
ANNUALIZATION_PERIODS = 8_760
RISK_FREE_RATE_HOURLY = Decimal(0)
SORTINO_TARGET_HOURLY = Decimal(0)
SORTINO_DENOMINATOR = "root_mean_square_shortfall_over_n"
BENCHMARK_ENTRY_POLICY = "post_warmup_same_latency"
CASH_STRATEGY_ID = "cash-baseline-v1"
BUY_AND_HOLD_STRATEGY_ID = "buy-and-hold-post-warmup-v1"
BENCHMARK_ADAPTER_MOMENTUM = Decimal(1)
SHORT_SAMPLE_RETURNS = 24
SYNTHETIC_STATEMENT = (
    "SYNTHETIC MECHANICS DEMONSTRATION. This report is not actual BTCUSDT "
    "performance evidence."
)


class AnalyticsError(ResearchError):
    """A research report cannot be built from the supplied inputs."""


class AnalyticsIntegrityError(AnalyticsError):
    """Equity inputs violate the causal accounting contract."""


class UndefinedReason(StrEnum):
    """Why a metric has no numeric value."""

    INCOMPLETE_HOURLY_GRID = "incomplete_hourly_grid"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    ZERO_DISPERSION = "zero_dispersion"
    ZERO_DOWNSIDE_DEVIATION = "zero_downside_deviation"
    BINARY64_UNDEFINED = "binary64_undefined"


@dataclass(frozen=True)
class CostScenario:
    """One pre-specified fee and slippage pair. These are not venue rates."""

    name: str
    fee_rate: Decimal
    slippage_rate: Decimal


DEFAULT_SCENARIOS: tuple[CostScenario, ...] = (
    CostScenario("zero", Decimal(0), Decimal(0)),
    CostScenario("baseline", Decimal("0.001"), Decimal("0.0005")),
    CostScenario("pessimistic_2x", Decimal("0.002"), Decimal("0.001")),
)


@dataclass(frozen=True)
class PortfolioReturn:
    """One simple equity return, or an explicit reason it is absent."""

    time: datetime
    previous_time: datetime
    value: Decimal | None
    missing_reason: str | None
    previous_role: str

    def __post_init__(self) -> None:
        if (self.value is None) == (self.missing_reason is None):
            raise AnalyticsIntegrityError(
                "a portfolio return needs a value or an explicit missing reason"
            )
        if self.value is not None and not self.value.is_finite():
            raise AnalyticsIntegrityError("portfolio returns must be finite")


@dataclass(frozen=True)
class MetricResult:
    """One named metric. Undefined results stay empty rather than becoming zero."""

    name: str
    value: Decimal | float | None
    unit: str
    reason: UndefinedReason | None
    observation_count: int
    coverage: str

    def __post_init__(self) -> None:
        if (self.value is None) == (self.reason is None):
            raise AnalyticsIntegrityError(
                "a metric needs a value or an explicit undefined reason"
            )
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise AnalyticsIntegrityError("metric decimals must be finite")
        if isinstance(self.value, float) and self.value != self.value:
            raise AnalyticsIntegrityError("metric floats must be finite")
        if isinstance(self.value, float) and self.value in (
            float("inf"),
            float("-inf"),
        ):
            raise AnalyticsIntegrityError("metric floats must be finite")


@dataclass(frozen=True)
class SegmentCoverage:
    """A contiguous hourly stretch. It does not carry its own Sharpe."""

    start: datetime
    end: datetime
    return_count: int


@dataclass(frozen=True)
class SampleCoverage:
    """What the shared evaluation clock actually contained."""

    equity_count: int
    return_count: int
    gap_count: int
    hourly_grid_complete: bool
    opening_anchor_time: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    segments: tuple[SegmentCoverage, ...]
    short_sample: bool
    open_mark_count: int
    close_mark_count: int


@dataclass(frozen=True)
class PolicyResult:
    """Metrics for one policy under one cost scenario."""

    name: str
    strategy_config_id: str
    run_id: str
    signal_role: str
    metrics: tuple[MetricResult, ...]
    fill_count: int
    posted_fees: Decimal
    exposure_fraction: Decimal | None
    stale_count: int


@dataclass(frozen=True)
class ScenarioResult:
    """Momentum, cash, and buy-and-hold under one cost row."""

    scenario: CostScenario
    policies: tuple[PolicyResult, ...]


@dataclass(frozen=True)
class ResearchReport:
    """Deterministic synthetic study. The id excludes the wall clock."""

    report_id: str
    analytics_version: str
    data_origin: str
    statement: str
    strategy_config_id: str
    valuation_convention: str
    return_convention: str
    missing_data_policy: str
    benchmark_entry_policy: str
    annualization_periods: int
    risk_free_rate_hourly: Decimal
    sortino_target_hourly: Decimal
    sortino_denominator: str
    initial_cash: Decimal
    latency_bars: int
    quantity_quantum: Decimal
    cash_quantum: Decimal
    coverage: SampleCoverage
    market_gap_count: int
    scenarios: tuple[ScenarioResult, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.valuation_convention != VALUATION_CONVENTION:
            raise AnalyticsIntegrityError(
                "report valuation convention is not the M7 rule"
            )
        if self.return_convention != RETURN_CONVENTION:
            raise AnalyticsIntegrityError("report return convention is not the M7 rule")
        if self.analytics_version != ANALYTICS_VERSION:
            raise AnalyticsIntegrityError("report analytics version is not supported")
