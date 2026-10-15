"""Expected months and synthetic fixtures never become real Binance evidence."""

from datetime import UTC, datetime, timedelta

from quantforge.ingestion.models import SCHEMA_VERSION, TimestampUnit, YearMonth
from quantforge.quality.models import IssueCode, Severity, ValidationIssue
from quantforge.research.readiness import (
    EvidenceStatus,
    FindingSeverity,
    SyntheticMonthEvidence,
    assess_evidence,
    expected_archives,
    manifest_document_id,
)

HOUR = timedelta(hours=1)


def _evidence(period: YearMonth, **overrides) -> SyntheticMonthEvidence:
    archive = next(item for item in expected_archives() if item.period == period)
    start = datetime(period.year, period.month, 1, tzinfo=UTC)
    opens = tuple(start + index * HOUR for index in range(archive.expected_hours))
    payload = {
        "period": period,
        "archive_sha256": "a" * 64,
        "provenance_archive_sha256": "a" * 64,
        "provenance_sha256": "b" * 64,
        "schema_version": SCHEMA_VERSION,
        "timestamp_unit": archive.request.timestamp_unit,
        "open_times": opens,
    }
    payload.update(overrides)
    return SyntheticMonthEvidence(**payload)


def test_the_expected_manifest_has_sixty_partitioned_months():
    archives = expected_archives()
    keys = [f"{item.period.year:04d}-{item.period.month:02d}" for item in archives]

    assert len(archives) == 60
    assert len(set(keys)) == 60
    assert sum(item.partition == "train" for item in archives) == 36
    assert sum(item.partition == "validation" for item in archives) == 12
    assert sum(item.partition == "holdout" for item in archives) == 12
    assert manifest_document_id() == manifest_document_id()
    assert archives[0].expected_hours == 31 * 24
    assert archives[-1].period == YearMonth(2025, 12)


def test_absent_and_duplicate_months_fail_closed():
    empty = assess_evidence()
    january = next(
        item for item in expected_archives() if item.period == YearMonth(2021, 1)
    )
    duplicated = assess_evidence(
        (_evidence(YearMonth(2021, 1)), _evidence(YearMonth(2021, 1))),
        expected=(january,),
    )
    unexpected = assess_evidence(
        (_evidence(YearMonth(2024, 1)),),
        expected=tuple(
            item for item in expected_archives() if item.partition == "train"
        ),
    )

    assert all(
        month.status is EvidenceStatus.EXPECTED_NOT_ACQUIRED for month in empty.months
    )
    assert empty.synthetic_fixture_passed is False
    assert empty.empirical_table_allowed is False
    assert empty.real_source_verified is False
    assert any(item.code == "duplicate_month" for item in duplicated.findings)
    assert any(item.code == "unexpected_month" for item in unexpected.findings)


def test_hash_unit_and_coverage_failures_block_a_synthetic_fixture():
    period = YearMonth(2025, 1)
    archive = next(item for item in expected_archives() if item.period == period)
    mismatched = assess_evidence(
        (_evidence(period, provenance_archive_sha256="c" * 64),),
        expected=(archive,),
    )
    wrong_unit = assess_evidence(
        (_evidence(period, timestamp_unit=TimestampUnit.MILLISECONDS),),
        expected=(archive,),
    )
    short = _evidence(period)
    missing_hour = assess_evidence(
        (_evidence(period, open_times=short.open_times[:-1]),),
        expected=(archive,),
    )
    passed = assess_evidence((_evidence(period),), expected=(archive,))
    relabeled = assess_evidence(
        (_evidence(period, origin_label="verified_real_source"),),
        expected=(archive,),
    )

    assert any(item.code == "source_hash_mismatch" for item in mismatched.findings)
    assert any(item.code == "timestamp_unit_mismatch" for item in wrong_unit.findings)
    assert any(item.code == "hourly_coverage" for item in missing_hour.findings)
    assert passed.synthetic_fixture_passed is True
    assert passed.empirical_table_allowed is False
    assert all(
        month.status is EvidenceStatus.VERIFIED_SYNTHETIC_FIXTURE
        for month in passed.months
    )
    assert relabeled.real_source_verified is False
    assert all(
        month.status is not EvidenceStatus.VERIFIED_REAL_SOURCE
        for month in relabeled.months
    )


def test_quality_warnings_are_kept_and_errors_fail():
    period = YearMonth(2021, 2)
    archive = next(item for item in expected_archives() if item.period == period)
    warning = ValidationIssue(
        severity=Severity.WARNING,
        code=IssueCode.ZERO_VOLUME,
        message="volume is zero",
    )
    error = ValidationIssue(
        severity=Severity.ERROR,
        code=IssueCode.NONPOSITIVE_PRICE,
        message="close is not positive",
    )
    warned = assess_evidence(
        (_evidence(period, issues=(warning,)),),
        expected=(archive,),
    )
    failed = assess_evidence(
        (_evidence(period, issues=(error,)),),
        expected=(archive,),
    )

    assert any(
        item.severity is FindingSeverity.WARNING and item.code == "zero_volume"
        for item in warned.findings
    )
    assert warned.synthetic_fixture_passed is True
    assert any(item.code == "nonpositive_price" for item in failed.findings)
    assert failed.synthetic_fixture_passed is False


def test_close_time_must_stay_inside_the_open_hour():
    period = YearMonth(2025, 12)
    archive = next(item for item in expected_archives() if item.period == period)
    evidence = _evidence(period)
    tick = timedelta(microseconds=1)
    valid = tuple(item + HOUR - tick for item in evidence.open_times)
    crossed = valid[:-1] + (evidence.open_times[-1] + HOUR,)
    accepted = assess_evidence(
        (_evidence(period, close_times=valid),),
        expected=(archive,),
    )
    rejected = assess_evidence(
        (_evidence(period, close_times=crossed),),
        expected=(archive,),
    )

    assert accepted.synthetic_fixture_passed is True
    assert any(item.code == "close_time_mismatch" for item in rejected.findings)
    assert rejected.empirical_table_allowed is False


def test_calendar_lengths_and_source_defects_fail_closed():
    archives = {item.period: item for item in expected_archives()}
    january = archives[YearMonth(2021, 1)]
    february_2023 = archives[YearMonth(2023, 2)]
    february_2024 = archives[YearMonth(2024, 2)]

    assert january.expected_hours == 31 * 24
    assert january.partition == "train"
    assert february_2023.expected_hours == 28 * 24
    assert february_2024.expected_hours == 29 * 24 == 696
    assert february_2024.partition == "validation"
    assert archives[YearMonth(2023, 12)].partition == "train"
    assert archives[YearMonth(2024, 1)].partition == "validation"
    assert archives[YearMonth(2024, 12)].partition == "validation"
    assert archives[YearMonth(2025, 1)].partition == "holdout"
    assert manifest_document_id() != manifest_document_id((january,))

    evidence = _evidence(YearMonth(2024, 2))
    assert evidence.open_times[0] == datetime(2024, 2, 1, tzinfo=UTC)
    assert evidence.open_times[-1] == datetime(2024, 2, 29, 23, tzinfo=UTC)
    passed = assess_evidence((evidence,), expected=(february_2024,))
    reversed_hours = (
        evidence.open_times[1],
        evidence.open_times[0],
        *evidence.open_times[2:],
    )
    ordered = assess_evidence(
        (_evidence(YearMonth(2024, 2), open_times=reversed_hours),),
        expected=(february_2024,),
    )
    schema = assess_evidence(
        (_evidence(YearMonth(2024, 2), schema_version="not-the-ingestion-schema"),),
        expected=(february_2024,),
    )
    provenance = assess_evidence(
        (_evidence(YearMonth(2024, 2), provenance_sha256=""),),
        expected=(february_2024,),
    )

    assert passed.synthetic_fixture_passed is True
    assert passed.real_source_verified is False
    assert any(item.code == "candle_order" for item in ordered.findings)
    assert any(item.code == "schema_mismatch" for item in schema.findings)
    assert any(item.code == "missing_provenance" for item in provenance.findings)
    assert passed.preflight_id != ordered.preflight_id
