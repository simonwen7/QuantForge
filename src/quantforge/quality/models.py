"""Models for deterministic market-data quality assessment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from quantforge.ingestion.models import Kline, MonthlyKlineRequest, ProvenanceRecord

VALIDATION_VERSION = "1"


class Severity(StrEnum):
    """Whether an issue blocks storage publication."""

    ERROR = "error"
    WARNING = "warning"


class IssueCode(StrEnum):
    """Stable machine-readable data-quality issue codes."""

    SOURCE_METADATA_MISMATCH = "source_metadata_mismatch"
    ROW_COUNT_MISMATCH = "row_count_mismatch"
    MISSING_CANDLE = "missing_candle"
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    OUT_OF_ORDER = "out_of_order"
    OPEN_TIME_NOT_HOURLY = "open_time_not_hourly"
    OPEN_TIME_OUTSIDE_MONTH = "open_time_outside_month"
    CLOSE_TIME_MISMATCH = "close_time_mismatch"
    NONFINITE_DECIMAL = "nonfinite_decimal"
    DECIMAL_PRECISION_EXCEEDED = "decimal_precision_exceeded"
    NONPOSITIVE_PRICE = "nonpositive_price"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    NEGATIVE_VOLUME = "negative_volume"
    NEGATIVE_TRADE_COUNT = "negative_trade_count"
    TAKER_VOLUME_EXCEEDS_TOTAL = "taker_volume_exceeds_total"
    ZERO_VOLUME = "zero_volume"
    ZERO_TRADES = "zero_trades"
    IGNORE_NONZERO = "ignore_nonzero"
    MISSING_MONTH = "missing_month"
    DUPLICATE_MONTH = "duplicate_month"
    OUT_OF_ORDER_MONTH = "out_of_order_month"
    CROSS_MONTH_GAP = "cross_month_gap"


@dataclass(frozen=True)
class ValidationIssue:
    """One deterministic error or warning tied to source context."""

    severity: Severity
    code: IssueCode
    message: str
    row_index: int | None = None
    open_time: datetime | None = None
    context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True)
class ValidationReport:
    """Quality result for one source month."""

    request: MonthlyKlineRequest
    source_sha256: str
    expected_rows: int
    observed_rows: int
    issues: tuple[ValidationIssue, ...]
    validation_version: str = VALIDATION_VERSION

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.severity is Severity.WARNING
        )

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class MonthAssessment:
    """Unmodified observations and their independently computed report."""

    request: MonthlyKlineRequest
    observations: tuple[Kline, ...]
    provenance: ProvenanceRecord
    report: ValidationReport


@dataclass(frozen=True)
class DatasetValidationReport:
    """Cross-month coverage and continuity findings."""

    covered_months: tuple[str, ...]
    expected_months: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]
    validation_version: str = VALIDATION_VERSION

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def is_valid(self) -> bool:
        return not self.errors
