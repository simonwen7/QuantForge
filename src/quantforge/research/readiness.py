"""Expected archive manifest and synthetic preflight checks.

An expected month is a calendar identity. It is not evidence that a file was
downloaded. A fixture that passes these checks remains a synthetic fixture.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from quantforge.ingestion.binance_vision import full_history_requests
from quantforge.ingestion.models import (
    SCHEMA_VERSION,
    MonthlyKlineRequest,
    TimestampUnit,
    YearMonth,
)
from quantforge.quality.klines import expected_hourly_rows
from quantforge.quality.models import Severity, ValidationIssue
from quantforge.research.experiment import experiment_id
from quantforge.research.models import ResearchError

_HOUR = timedelta(hours=1)


class ReadinessError(ResearchError):
    """A readiness input cannot be assessed."""


class EvidenceStatus(StrEnum):
    """Acquisition label for one expected month."""

    EXPECTED_NOT_ACQUIRED = "EXPECTED_NOT_ACQUIRED"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED_SYNTHETIC_FIXTURE = "VERIFIED_SYNTHETIC_FIXTURE"
    VERIFIED_REAL_SOURCE = "VERIFIED_REAL_SOURCE"
    FAILED_INTEGRITY = "FAILED_INTEGRITY"


class FindingSeverity(StrEnum):
    """How a preflight finding limits a future study."""

    HARD_FAIL = "hard_fail"
    WARNING = "warning"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class ExpectedArchive:
    """One month the research calendar expects. The file may be absent."""

    request: MonthlyKlineRequest
    partition: str
    expected_hours: int

    @property
    def period(self) -> YearMonth:
        return self.request.period


@dataclass(frozen=True)
class SyntheticMonthEvidence:
    """In-memory fixture evidence. This object does not open a file."""

    period: YearMonth
    archive_sha256: str
    provenance_archive_sha256: str
    provenance_sha256: str
    schema_version: str
    timestamp_unit: TimestampUnit
    open_times: tuple[datetime, ...]
    close_times: tuple[datetime, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()
    origin_label: str = "synthetic_fixture"


@dataclass(frozen=True)
class PreflightFinding:
    """One deterministic readiness finding."""

    severity: FindingSeverity
    code: str
    message: str
    period: str | None = None


@dataclass(frozen=True)
class MonthAssessment:
    """Status of one expected archive after the supplied evidence is applied."""

    period: str
    partition: str
    status: EvidenceStatus
    expected_hours: int


@dataclass(frozen=True)
class PreflightResult:
    """Manifest identity plus the findings from supplied evidence."""

    manifest_id: str
    preflight_id: str
    months: tuple[MonthAssessment, ...]
    findings: tuple[PreflightFinding, ...]
    synthetic_fixture_passed: bool
    empirical_table_allowed: bool
    real_source_verified: bool

    def __post_init__(self) -> None:
        if self.empirical_table_allowed or self.real_source_verified:
            raise ReadinessError(
                "a preflight cannot authorize an empirical table or a real source"
            )
        if any(
            month.status is EvidenceStatus.VERIFIED_REAL_SOURCE for month in self.months
        ):
            raise ReadinessError("synthetic preflight cannot verify a real source")


def partition_name(period: YearMonth) -> str:
    """Classify a month by the approved research partitions."""
    if period.year <= 2023:
        return "train"
    if period.year == 2024:
        return "validation"
    if period.year == 2025:
        return "holdout"
    raise ReadinessError(
        f"month {period.year:04d}-{period.month:02d} is outside 2021-2025"
    )


def expected_archives() -> tuple[ExpectedArchive, ...]:
    """Return the 60 BTCUSDT spot 1h months from 2021-01 through 2025-12."""
    return tuple(
        ExpectedArchive(
            request=request,
            partition=partition_name(request.period),
            expected_hours=expected_hourly_rows(request.period),
        )
        for request in full_history_requests()
    )


def _period_key(period: YearMonth) -> str:
    return f"{period.year:04d}-{period.month:02d}"


def _sha256_text(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _manifest_document(archives: Sequence[ExpectedArchive]) -> dict[str, object]:
    return {
        "archives": [
            {
                "expected_hours": archive.expected_hours,
                "filename": archive.request.archive_filename,
                "interval": archive.request.interval,
                "market": archive.request.market,
                "partition": archive.partition,
                "period": _period_key(archive.period),
                "symbol": archive.request.symbol,
                "timestamp_unit": archive.request.timestamp_unit.value,
            }
            for archive in archives
        ],
        "instrument": "BTCUSDT",
        "interval": "1h",
        "market": "spot",
    }


def _close_tick(request: MonthlyKlineRequest) -> timedelta:
    if request.timestamp_unit is TimestampUnit.MILLISECONDS:
        return timedelta(milliseconds=1)
    if request.timestamp_unit is TimestampUnit.MICROSECONDS:
        return timedelta(microseconds=1)
    raise ReadinessError(f"unsupported timestamp unit: {request.timestamp_unit!r}")


def _expected_opens(request: MonthlyKlineRequest) -> tuple[datetime, ...]:
    start = datetime(request.period.year, request.period.month, 1, tzinfo=UTC)
    count = expected_hourly_rows(request.period)
    return tuple(start + index * _HOUR for index in range(count))


def _assess_month(
    archive: ExpectedArchive,
    evidence: SyntheticMonthEvidence,
) -> tuple[EvidenceStatus, list[PreflightFinding]]:
    period = _period_key(archive.period)
    findings: list[PreflightFinding] = []
    request = archive.request
    if evidence.origin_label != "synthetic_fixture":
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "real_source_relabel_rejected",
                "a fixture cannot be labeled as verified real Binance data",
                period,
            )
        )
    if not evidence.provenance_sha256 or not _sha256_text(evidence.provenance_sha256):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "missing_provenance",
                "provenance sha256 is missing or malformed",
                period,
            )
        )
    if (
        not _sha256_text(evidence.archive_sha256)
        or evidence.archive_sha256 != evidence.provenance_archive_sha256
    ):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "source_hash_mismatch",
                "archive sha256 does not match provenance",
                period,
            )
        )
    if evidence.schema_version != SCHEMA_VERSION:
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "schema_mismatch",
                "schema version does not match the ingestion contract",
                period,
            )
        )
    if evidence.timestamp_unit is not request.timestamp_unit:
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "timestamp_unit_mismatch",
                "timestamp unit does not match the month convention",
                period,
            )
        )
    expected_opens = _expected_opens(request)
    observed = evidence.open_times
    if any(item.tzinfo is not UTC for item in observed):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "open_time_not_utc",
                "open timestamps must use datetime.UTC",
                period,
            )
        )
    if any(item.minute or item.second or item.microsecond for item in observed):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "open_time_not_hourly",
                "an open timestamp is not an exact UTC hour",
                period,
            )
        )
    if any(left >= right for left, right in itertools.pairwise(observed)):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "candle_order",
                "open timestamps are duplicated or reversed",
                period,
            )
        )
    missing = sorted(set(expected_opens) - set(observed))
    unexpected = sorted(set(observed) - set(expected_opens))
    if missing or unexpected or len(observed) != archive.expected_hours:
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "hourly_coverage",
                f"expected {archive.expected_hours} hourly opens; "
                f"observed {len(observed)} with {len(missing)} missing",
                period,
            )
        )
    if evidence.close_times:
        if len(evidence.close_times) != len(observed):
            findings.append(
                PreflightFinding(
                    FindingSeverity.HARD_FAIL,
                    "close_time_mismatch",
                    "close timestamps do not align with open timestamps",
                    period,
                )
            )
        else:
            tick = _close_tick(request)
            for open_time, close_time in zip(
                observed, evidence.close_times, strict=True
            ):
                if close_time != open_time + _HOUR - tick:
                    findings.append(
                        PreflightFinding(
                            FindingSeverity.HARD_FAIL,
                            "close_time_mismatch",
                            "close timestamp does not match the 1h convention",
                            period,
                        )
                    )
                    break
    for issue in evidence.issues:
        if issue.severity is Severity.ERROR:
            findings.append(
                PreflightFinding(
                    FindingSeverity.HARD_FAIL,
                    issue.code.value,
                    issue.message,
                    period,
                )
            )
        elif issue.severity is Severity.WARNING:
            findings.append(
                PreflightFinding(
                    FindingSeverity.WARNING,
                    issue.code.value,
                    issue.message,
                    period,
                )
            )
        else:
            findings.append(
                PreflightFinding(
                    FindingSeverity.HARD_FAIL,
                    "unknown_issue_severity",
                    "an unclassified quality issue fails closed",
                    period,
                )
            )
    failed = any(item.severity is FindingSeverity.HARD_FAIL for item in findings)
    status = (
        EvidenceStatus.FAILED_INTEGRITY
        if failed
        else EvidenceStatus.VERIFIED_SYNTHETIC_FIXTURE
    )
    return status, findings


def assess_evidence(
    evidence: Sequence[SyntheticMonthEvidence] = (),
    *,
    expected: Sequence[ExpectedArchive] | None = None,
) -> PreflightResult:
    """Compare fixture evidence with the expected manifest. No file is opened."""
    archives = tuple(expected) if expected is not None else expected_archives()
    if not archives:
        raise ReadinessError("expected archive manifest cannot be empty")
    by_period: dict[YearMonth, list[SyntheticMonthEvidence]] = {}
    findings: list[PreflightFinding] = []
    expected_periods = {archive.period for archive in archives}
    for item in evidence:
        if item.period not in expected_periods:
            findings.append(
                PreflightFinding(
                    FindingSeverity.HARD_FAIL,
                    "unexpected_month",
                    "evidence names a month outside the expected manifest",
                    _period_key(item.period),
                )
            )
        by_period.setdefault(item.period, []).append(item)
    months: list[MonthAssessment] = []
    for archive in archives:
        supplied = by_period.get(archive.period, [])
        key = _period_key(archive.period)
        if not supplied:
            months.append(
                MonthAssessment(
                    period=key,
                    partition=archive.partition,
                    status=EvidenceStatus.EXPECTED_NOT_ACQUIRED,
                    expected_hours=archive.expected_hours,
                )
            )
            continue
        if len(supplied) > 1:
            findings.append(
                PreflightFinding(
                    FindingSeverity.HARD_FAIL,
                    "duplicate_month",
                    "the same month was supplied more than once",
                    key,
                )
            )
            months.append(
                MonthAssessment(
                    period=key,
                    partition=archive.partition,
                    status=EvidenceStatus.FAILED_INTEGRITY,
                    expected_hours=archive.expected_hours,
                )
            )
            continue
        status, month_findings = _assess_month(archive, supplied[0])
        findings.extend(month_findings)
        months.append(
            MonthAssessment(
                period=key,
                partition=archive.partition,
                status=status,
                expected_hours=archive.expected_hours,
            )
        )
    if any(
        month.status is not EvidenceStatus.VERIFIED_SYNTHETIC_FIXTURE
        for month in months
    ):
        findings.append(
            PreflightFinding(
                FindingSeverity.HARD_FAIL,
                "incomplete_strict_coverage",
                "strict partition coverage is not satisfied by acquired months",
                None,
            )
        )
    hard_fail = any(item.severity is FindingSeverity.HARD_FAIL for item in findings)
    manifest_document = _manifest_document(archives)
    manifest_id = experiment_id(manifest_document)
    preflight_document = {
        "findings": [
            {
                "code": item.code,
                "message": item.message,
                "period": item.period,
                "severity": item.severity.value,
            }
            for item in findings
        ],
        "manifest_id": manifest_id,
        "months": [
            {"period": month.period, "status": month.status.value} for month in months
        ],
    }
    return PreflightResult(
        manifest_id=manifest_id,
        preflight_id=experiment_id(preflight_document),
        months=tuple(months),
        findings=tuple(findings),
        synthetic_fixture_passed=not hard_fail,
        empirical_table_allowed=False,
        real_source_verified=False,
    )


def manifest_document_id(archives: Sequence[ExpectedArchive] | None = None) -> str:
    """Hash the expected calendar. Acquisition evidence is not included."""
    selected = tuple(archives) if archives is not None else expected_archives()
    return experiment_id(_manifest_document(selected))


def human_review_topics() -> tuple[str, ...]:
    """Questions that remain for a person even after a clean fixture."""
    return (
        "acceptance of economically meaningful warnings",
        "treatment of structurally valid but unusual observations",
        "data-source terms and redistribution conditions",
        "whether publication of derived results is permitted",
    )


def finding_payload(finding: PreflightFinding) -> Mapping[str, object]:
    """Serialize one finding for a report hash."""
    return {
        "code": finding.code,
        "message": finding.message,
        "period": finding.period,
        "severity": finding.severity.value,
    }
