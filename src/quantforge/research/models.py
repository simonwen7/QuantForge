"""Immutable research data, protocol, and experiment models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from quantforge.ingestion.models import YearMonth
from quantforge.quality.models import ValidationIssue

RESEARCH_PROTOCOL_VERSION = "1"
RETURN_DECIMAL_PRECISION = 50
DEFAULT_PERIODS_PER_YEAR = 8_760
AVAILABILITY_RULE = "available_at = open_time + 1 hour"
SAMPLE_STANDARD_DEVIATION = "sample standard deviation with denominator n - 1"
ANNUALIZATION_NOTE = (
    "8760 is a conventional 365-day hourly factor, not the exact number of "
    "observations in every calendar year"
)
LABEL_AVAILABILITY_RULE = (
    "a close label is eligible only when the labeled candle's available_at is "
    "strictly before the exclusive split end and that candle is present; "
    "availability exactly at the split boundary is excluded"
)
STATISTIC_ALIGNMENT_RULE = (
    "a rolling statistic at index i uses only inputs through index i and is "
    "available at the same time as input i"
)
AUTOCORRELATION_DEFINITION = (
    "Pearson correlation of the two lag-aligned samples; zero variance is undefined"
)
QUANTILE_CONVENTION = "Hyndman-Fan type 7 linear interpolation"
FLOAT_BOUNDARY_NOTE = (
    "standard deviation, volatility, autocorrelation, and quantiles convert "
    "finite Decimal values to binary64; a result is undefined when a value is "
    "non-finite in binary64, underflows to zero, or a successive nonzero gap "
    "is at most 1000 times a 1e-15 local roundoff or differs after conversion "
    "by more than a relative tolerance of 1e-3. Correlations more than 1e-10 "
    "outside [-1, 1] are undefined, and smaller excesses are clamped"
)


class ResearchError(Exception):
    """Base class for expected research-workflow failures."""


class ResearchDataError(ResearchError):
    """Verified research observations could not be constructed."""


class HoldoutAccessError(ResearchError):
    """Final-holdout observations were requested without authorization."""


class CoverageError(ResearchError):
    """Requested dataset coverage is incomplete, duplicated, or unexpected."""


class ReturnCalculationError(ResearchError):
    """A return cannot be calculated from the supplied prices."""


class StatisticError(ResearchError):
    """A statistic is undefined or its inputs violate the research contract."""


class LookaheadError(ResearchError):
    """A calculation or decision would use data before it was available."""


class ResearchAccessMode(StrEnum):
    """Whether final-holdout observations may be materialized."""

    DEVELOPMENT = "development"
    FINAL_EVALUATION = "final_evaluation"


class CoveragePolicy(StrEnum):
    """How requested months are compared with loaded months."""

    STRICT = "strict"
    EXPLICIT_PARTIAL = "explicit_partial"


class ReturnKind(StrEnum):
    """Supported close-to-close return definitions."""

    SIMPLE = "simple"
    LOG = "log"


class MissingReason(StrEnum):
    """Why a research value is explicitly absent."""

    INITIAL = "initial"
    GAP = "gap"
    INVALID_PRICE = "invalid_price"
    INCOMPLETE_WINDOW = "incomplete_window"
    UNDEFINED = "undefined"


class SplitName(StrEnum):
    """Chronological research partitions."""

    TRAIN = "train"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


def candle_available_at(open_time: datetime) -> datetime:
    """Return the conservative completion time of a one-hour candle.

    Binance ``close_time`` is the last millisecond or microsecond inside the
    candle. Close-derived research values are not treated as observable until
    the following UTC hour begins.
    """
    if open_time.tzinfo is not UTC:
        raise ResearchDataError("candle open_time must use datetime.UTC")
    return open_time + timedelta(hours=1)


@dataclass(frozen=True)
class PriceObservation:
    """One verified hourly close and its research availability time."""

    open_time: datetime
    close_time: datetime
    close: Decimal
    source_archive_sha256: str
    source_year: int
    source_month: int
    source_schema_version: str
    available_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC or self.close_time.tzinfo is not UTC:
            raise ResearchDataError("research timestamps must use datetime.UTC")
        if self.close_time <= self.open_time:
            raise ResearchDataError("close_time must follow open_time")
        if not self.close.is_finite() or self.close <= 0:
            raise ResearchDataError("close price must be finite and positive")
        object.__setattr__(self, "available_at", candle_available_at(self.open_time))


@dataclass(frozen=True)
class CoverageGap:
    """A break between two successive loaded hourly observations."""

    previous_open_time: datetime
    next_open_time: datetime


@dataclass(frozen=True)
class PartitionLineage:
    """Checksum lineage for one verified processed month."""

    year: int
    month: int
    source_archive_sha256: str
    source_provenance_sha256: str
    parquet_sha256: str
    validation_sha256: str
    observation_count: int
    warning_count: int


@dataclass(frozen=True)
class ResearchDataset:
    """Verified observations and explicit coverage relative to a request.

    ``is_complete`` means the loaded observations exactly cover the requested
    periods without gaps. It does not mean the full 2021-2025 history is present.
    """

    observations: tuple[PriceObservation, ...]
    expected_periods: tuple[YearMonth, ...]
    covered_periods: tuple[YearMonth, ...]
    gaps: tuple[CoverageGap, ...]
    lineage: tuple[PartitionLineage, ...]
    warnings: tuple[ValidationIssue, ...]
    access_mode: ResearchAccessMode
    coverage_policy: CoveragePolicy
    final_evaluation_authorized: bool
    is_complete: bool

    @property
    def covers_approved_history(self) -> bool:
        """Whether this request is the complete approved 60-month history."""
        return (
            self.is_complete
            and len(self.expected_periods) == 60
            and self.expected_periods[0] == YearMonth(2021, 1)
            and self.expected_periods[-1] == YearMonth(2025, 12)
        )


@dataclass(frozen=True)
class ReturnObservation:
    """A close-to-close return aligned to the current candle."""

    open_time: datetime
    close_time: datetime
    available_at: datetime
    kind: ReturnKind
    value: Decimal | None
    missing_reason: MissingReason | None

    def __post_init__(self) -> None:
        if self.available_at != candle_available_at(self.open_time):
            raise ResearchDataError(
                "return availability must be one hour after its candle open_time"
            )
        if (self.value is None) == (self.missing_reason is None):
            raise ResearchDataError(
                "a return must contain either a value or an explicit missing reason"
            )
        if self.value is not None and not self.value.is_finite():
            raise ResearchDataError("return values must be finite")


@dataclass(frozen=True)
class StatisticPoint:
    """One trailing-window result or an explicit reason for its absence."""

    value: Decimal | float | None
    missing_reason: MissingReason | None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.missing_reason is None):
            raise StatisticError(
                "a statistic must contain either a value or an explicit missing reason"
            )
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise StatisticError("statistic values must be finite")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise StatisticError("statistic values must be finite")


@dataclass(frozen=True)
class DescriptiveSummary:
    """Complete-sample return summary using documented conventions."""

    count: int
    mean: Decimal
    sample_standard_deviation: float
    minimum: Decimal
    maximum: Decimal
    quantile_25: float
    median: float
    quantile_75: float

    def __post_init__(self) -> None:
        if self.count < 2:
            raise StatisticError("summary requires at least two finite observations")
        if not self.mean.is_finite():
            raise StatisticError("summary mean must be finite")
        if any(
            not math.isfinite(value)
            for value in (
                self.sample_standard_deviation,
                self.quantile_25,
                self.median,
                self.quantile_75,
            )
        ):
            raise StatisticError("summary floating-point values must be finite")


@dataclass(frozen=True)
class FeatureSpecification:
    """Parameters that define a reproducible research feature set."""

    return_kind: ReturnKind
    rolling_window: int
    autocorrelation_lags: tuple[int, ...]
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR
    ddof: int = 1
    decimal_precision: int = RETURN_DECIMAL_PRECISION
    label_horizon_hours: int = 0
    warmup_observations: int = 0

    def __post_init__(self) -> None:
        if self.rolling_window < 2:
            raise StatisticError("rolling_window must be at least 2")
        if not self.autocorrelation_lags or any(
            lag < 1 for lag in self.autocorrelation_lags
        ):
            raise StatisticError("autocorrelation lags must be positive")
        if self.periods_per_year <= 0:
            raise StatisticError("periods_per_year must be positive")
        if self.ddof not in (0, 1):
            raise StatisticError("ddof must be 0 or 1")
        if self.decimal_precision != RETURN_DECIMAL_PRECISION:
            raise ReturnCalculationError(
                f"M4 return precision is fixed at {RETURN_DECIMAL_PRECISION}"
            )
        if self.label_horizon_hours < 0 or self.warmup_observations < 0:
            raise ResearchError(
                "feature horizons and warm-up counts cannot be negative"
            )
