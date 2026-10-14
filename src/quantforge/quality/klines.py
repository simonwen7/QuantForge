"""Deterministic financial-data quality rules for monthly Spot klines."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.ingestion.binance_vision import archive_url, checksum_url
from quantforge.ingestion.models import (
    DATASET_TERMS_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    Kline,
    MonthlyKlineRequest,
    ProvenanceRecord,
    TimestampUnit,
    YearMonth,
)
from quantforge.quality.models import (
    DatasetValidationReport,
    IssueCode,
    MonthAssessment,
    Severity,
    ValidationIssue,
    ValidationReport,
)

DECIMAL_PRECISION = 38
DECIMAL_SCALE = 18
_HOUR = timedelta(hours=1)
_PRICE_FIELDS = ("open", "high", "low", "close")
_VOLUME_FIELDS = (
    "volume",
    "quote_asset_volume",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
)
_DECIMAL_FIELDS = _PRICE_FIELDS + _VOLUME_FIELDS + ("ignore",)


def expected_hourly_rows(period: YearMonth) -> int:
    """Return the exact number of UTC hours in a calendar month."""
    return calendar.monthrange(period.year, period.month)[1] * 24


def decimal_fits_storage(value: Decimal) -> bool:
    """Whether a finite Decimal fits DECIMAL(38,18) without rounding."""
    if not value.is_finite():
        return False
    _, digits, exponent = value.as_tuple()
    scale = max(-exponent, 0)
    integer_digits = (
        len(digits) + exponent if exponent >= 0 else max(len(digits) - scale, 0)
    )
    precision = integer_digits + scale
    return (
        scale <= DECIMAL_SCALE
        and integer_digits <= DECIMAL_PRECISION - DECIMAL_SCALE
        and precision <= DECIMAL_PRECISION
    )


def _issue(
    severity: Severity,
    code: IssueCode,
    message: str,
    *,
    row_index: int | None = None,
    open_time: datetime | None = None,
    context: dict[str, str] | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        row_index=row_index,
        open_time=open_time,
        context=context or {},
    )


def _month_bounds(request: MonthlyKlineRequest) -> tuple[datetime, datetime]:
    start = datetime(request.period.year, request.period.month, 1, tzinfo=UTC)
    if request.period.month == 12:
        end = datetime(request.period.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(
            request.period.year,
            request.period.month + 1,
            1,
            tzinfo=UTC,
        )
    return start, end


def _metadata_issues(
    request: MonthlyKlineRequest,
    provenance: ProvenanceRecord,
) -> list[ValidationIssue]:
    expected = {
        "source_name": SOURCE_NAME,
        "archive_url": archive_url(request),
        "checksum_url": checksum_url(request),
        "symbol": request.symbol,
        "market": request.market,
        "interval": request.interval,
        "year": request.period.year,
        "month": request.period.month,
        "source_timestamp_unit": request.timestamp_unit,
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "dataset_terms_url": DATASET_TERMS_URL,
        "archive_member_name": request.csv_filename,
    }
    issues: list[ValidationIssue] = []
    for field_name, expected_value in expected.items():
        actual_value = getattr(provenance, field_name)
        if actual_value != expected_value:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.SOURCE_METADATA_MISMATCH,
                    f"source provenance {field_name} does not match the request",
                    context={
                        "field": field_name,
                        "expected": str(expected_value),
                        "actual": str(actual_value),
                    },
                )
            )
    return issues


def _validate_decimal_fields(
    kline: Kline,
    row_index: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field_name in _DECIMAL_FIELDS:
        value = getattr(kline, field_name)
        if not value.is_finite():
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.NONFINITE_DECIMAL,
                    f"{field_name} must be finite",
                    row_index=row_index,
                    open_time=kline.open_time,
                    context={"field": field_name, "value": str(value)},
                )
            )
        elif not decimal_fits_storage(value):
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.DECIMAL_PRECISION_EXCEEDED,
                    f"{field_name} cannot be represented as DECIMAL(38,18)",
                    row_index=row_index,
                    open_time=kline.open_time,
                    context={"field": field_name, "value": str(value)},
                )
            )
    return issues


def _validate_financial_values(
    kline: Kline,
    row_index: int,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    finite_prices = all(getattr(kline, name).is_finite() for name in _PRICE_FIELDS)
    if finite_prices:
        for field_name in _PRICE_FIELDS:
            value = getattr(kline, field_name)
            if value <= 0:
                issues.append(
                    _issue(
                        Severity.ERROR,
                        IssueCode.NONPOSITIVE_PRICE,
                        f"{field_name} must be positive",
                        row_index=row_index,
                        open_time=kline.open_time,
                        context={"field": field_name, "value": str(value)},
                    )
                )
        if not (
            kline.low <= kline.open <= kline.high
            and kline.low <= kline.close <= kline.high
        ):
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.OHLC_INCONSISTENT,
                    "OHLC values violate low <= open/close <= high",
                    row_index=row_index,
                    open_time=kline.open_time,
                )
            )

    for field_name in _VOLUME_FIELDS:
        value = getattr(kline, field_name)
        if value.is_finite() and value < 0:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.NEGATIVE_VOLUME,
                    f"{field_name} cannot be negative",
                    row_index=row_index,
                    open_time=kline.open_time,
                    context={"field": field_name, "value": str(value)},
                )
            )

    if (
        kline.volume.is_finite()
        and kline.taker_buy_base_asset_volume.is_finite()
        and kline.taker_buy_base_asset_volume > kline.volume
    ):
        issues.append(
            _issue(
                Severity.ERROR,
                IssueCode.TAKER_VOLUME_EXCEEDS_TOTAL,
                "taker-buy base volume exceeds total base volume",
                row_index=row_index,
                open_time=kline.open_time,
            )
        )
    if (
        kline.quote_asset_volume.is_finite()
        and kline.taker_buy_quote_asset_volume.is_finite()
        and kline.taker_buy_quote_asset_volume > kline.quote_asset_volume
    ):
        issues.append(
            _issue(
                Severity.ERROR,
                IssueCode.TAKER_VOLUME_EXCEEDS_TOTAL,
                "taker-buy quote volume exceeds total quote volume",
                row_index=row_index,
                open_time=kline.open_time,
            )
        )
    if kline.number_of_trades < 0:
        issues.append(
            _issue(
                Severity.ERROR,
                IssueCode.NEGATIVE_TRADE_COUNT,
                "number_of_trades cannot be negative",
                row_index=row_index,
                open_time=kline.open_time,
            )
        )

    zero_volume_fields = [
        field_name
        for field_name in ("volume", "quote_asset_volume")
        if getattr(kline, field_name).is_finite()
        and getattr(kline, field_name).is_zero()
    ]
    if zero_volume_fields:
        issues.append(
            _issue(
                Severity.WARNING,
                IssueCode.ZERO_VOLUME,
                "one or more total-volume fields are zero",
                row_index=row_index,
                open_time=kline.open_time,
                context={"fields": ",".join(zero_volume_fields)},
            )
        )
    if kline.number_of_trades == 0:
        issues.append(
            _issue(
                Severity.WARNING,
                IssueCode.ZERO_TRADES,
                "number_of_trades is zero",
                row_index=row_index,
                open_time=kline.open_time,
            )
        )
    if kline.ignore.is_finite() and not kline.ignore.is_zero():
        issues.append(
            _issue(
                Severity.WARNING,
                IssueCode.IGNORE_NONZERO,
                "the Binance ignore field is nonzero",
                row_index=row_index,
                open_time=kline.open_time,
                context={"value": str(kline.ignore)},
            )
        )
    return issues


def validate_month(
    request: MonthlyKlineRequest,
    observations: Iterable[Kline],
    provenance: ProvenanceRecord,
) -> MonthAssessment:
    """Assess one month without changing source values or source order."""
    rows = tuple(observations)
    issues = _metadata_issues(request, provenance)
    expected_count = expected_hourly_rows(request.period)
    if len(rows) != expected_count:
        issues.append(
            _issue(
                Severity.ERROR,
                IssueCode.ROW_COUNT_MISMATCH,
                f"observed {len(rows)} rows; expected {expected_count}",
                context={
                    "observed": str(len(rows)),
                    "expected": str(expected_count),
                },
            )
        )

    month_start, next_month_start = _month_bounds(request)
    expected_timestamps = {
        month_start + offset * _HOUR for offset in range(expected_count)
    }
    observed_timestamps: set[datetime] = set()
    previous_open: datetime | None = None
    close_tick = (
        timedelta(milliseconds=1)
        if request.timestamp_unit is TimestampUnit.MILLISECONDS
        else timedelta(microseconds=1)
    )

    for row_index, kline in enumerate(rows, start=1):
        if kline.open_time in observed_timestamps:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.DUPLICATE_TIMESTAMP,
                    "open timestamp is duplicated",
                    row_index=row_index,
                    open_time=kline.open_time,
                )
            )
        if previous_open is not None and kline.open_time <= previous_open:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.OUT_OF_ORDER,
                    "source rows are not strictly increasing by open timestamp",
                    row_index=row_index,
                    open_time=kline.open_time,
                    context={"previous_open_time": previous_open.isoformat()},
                )
            )
        previous_open = kline.open_time
        observed_timestamps.add(kline.open_time)

        if not month_start <= kline.open_time < next_month_start:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.OPEN_TIME_OUTSIDE_MONTH,
                    "open timestamp falls outside the requested UTC month",
                    row_index=row_index,
                    open_time=kline.open_time,
                )
            )
        if (
            kline.open_time.minute != 0
            or kline.open_time.second != 0
            or kline.open_time.microsecond != 0
        ):
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.OPEN_TIME_NOT_HOURLY,
                    "open timestamp is not aligned to an exact UTC hour",
                    row_index=row_index,
                    open_time=kline.open_time,
                )
            )

        expected_close = kline.open_time + _HOUR - close_tick
        if kline.close_time != expected_close:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.CLOSE_TIME_MISMATCH,
                    "close timestamp does not match the documented 1h convention",
                    row_index=row_index,
                    open_time=kline.open_time,
                    context={
                        "expected": expected_close.isoformat(),
                        "actual": kline.close_time.isoformat(),
                    },
                )
            )

        issues.extend(_validate_decimal_fields(kline, row_index))
        issues.extend(_validate_financial_values(kline, row_index))

    for missing_time in sorted(expected_timestamps - observed_timestamps):
        issues.append(
            _issue(
                Severity.ERROR,
                IssueCode.MISSING_CANDLE,
                "expected hourly candle is missing",
                open_time=missing_time,
            )
        )

    report = ValidationReport(
        request=request,
        source_sha256=provenance.actual_sha256,
        expected_rows=expected_count,
        observed_rows=len(rows),
        issues=tuple(issues),
    )
    return MonthAssessment(
        request=request,
        observations=rows,
        provenance=provenance,
        report=report,
    )


def _period_key(period: YearMonth) -> str:
    return f"{period.year:04d}-{period.month:02d}"


def _next_period(period: YearMonth) -> tuple[int, int]:
    if period.month == 12:
        return period.year + 1, 1
    return period.year, period.month + 1


def validate_month_sequence(
    months: Iterable[MonthAssessment],
    expected_periods: Iterable[YearMonth],
) -> DatasetValidationReport:
    """Validate requested coverage and continuity without relabeling subsets."""
    assessments = tuple(months)
    expected = tuple(expected_periods)
    issues: list[ValidationIssue] = []
    seen_periods: set[tuple[int, int]] = set()
    previous: MonthAssessment | None = None

    for assessment in assessments:
        period_tuple = (
            assessment.request.period.year,
            assessment.request.period.month,
        )
        if period_tuple in seen_periods:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.DUPLICATE_MONTH,
                    f"month {_period_key(assessment.request.period)} is duplicated",
                )
            )
        if previous is not None:
            previous_tuple = (
                previous.request.period.year,
                previous.request.period.month,
            )
            if period_tuple <= previous_tuple:
                issues.append(
                    _issue(
                        Severity.ERROR,
                        IssueCode.OUT_OF_ORDER_MONTH,
                        "monthly inputs are not strictly chronological",
                    )
                )
            if (
                period_tuple == _next_period(previous.request.period)
                and previous.observations
                and assessment.observations
            ):
                expected_open = previous.observations[-1].open_time + _HOUR
                actual_open = assessment.observations[0].open_time
                if actual_open != expected_open:
                    issues.append(
                        _issue(
                            Severity.ERROR,
                            IssueCode.CROSS_MONTH_GAP,
                            "adjacent months are not hourly-contiguous",
                            open_time=actual_open,
                            context={"expected": expected_open.isoformat()},
                        )
                    )
        seen_periods.add(period_tuple)
        previous = assessment

    for period in expected:
        period_tuple = (period.year, period.month)
        if period_tuple not in seen_periods:
            issues.append(
                _issue(
                    Severity.ERROR,
                    IssueCode.MISSING_MONTH,
                    f"expected month {_period_key(period)} is missing",
                )
            )

    return DatasetValidationReport(
        covered_months=tuple(
            _period_key(assessment.request.period) for assessment in assessments
        ),
        expected_months=tuple(_period_key(period) for period in expected),
        issues=tuple(issues),
    )
