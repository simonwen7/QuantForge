"""Offline tests for deterministic monthly kline quality rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from quantforge.ingestion import YearMonth
from quantforge.quality import (
    IssueCode,
    Severity,
    decimal_fits_storage,
    expected_hourly_rows,
    validate_month,
    validate_month_sequence,
)
from tests.support.synthetic_klines import (
    make_klines,
    make_request,
    write_synthetic_source,
)


def _assessment(tmp_path, year: int = 2024, month: int = 1):
    request = make_request(year, month)
    rows = make_klines(request)
    ingestion = write_synthetic_source(tmp_path, request, rows)
    return validate_month(request, rows, ingestion.provenance)


def _codes(report) -> set[IssueCode]:
    return {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2023, 2, 672),
        (2024, 2, 696),
        (2024, 4, 720),
        (2024, 1, 744),
    ],
)
def test_expected_rows_follow_calendar_months(tmp_path, year, month, expected):
    request = make_request(year, month)
    rows = make_klines(request)
    ingestion = write_synthetic_source(tmp_path, request, rows)

    assert expected_hourly_rows(request.period) == expected
    assert validate_month(request, rows, ingestion.provenance).report.is_valid


@pytest.mark.parametrize("missing_index", [0, 300, -1])
def test_missing_first_middle_and_last_candles_are_errors(tmp_path, missing_index):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows.pop(missing_index)

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.ROW_COUNT_MISMATCH in _codes(report)
    assert IssueCode.MISSING_CANDLE in _codes(report)
    assert not report.is_valid


def test_duplicate_and_out_of_order_rows_are_detected(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[4] = rows[3]
    rows[10], rows[11] = rows[11], rows[10]

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.DUPLICATE_TIMESTAMP in _codes(report)
    assert IssueCode.OUT_OF_ORDER in _codes(report)


def test_december_2024_and_january_2025_close_conventions(tmp_path):
    december = _assessment(tmp_path / "december", 2024, 12)
    january = _assessment(tmp_path / "january", 2025, 1)

    assert december.report.is_valid
    assert january.report.is_valid
    assert december.observations[0].close_time == december.observations[
        0
    ].open_time + timedelta(hours=1) - timedelta(milliseconds=1)
    assert january.observations[0].close_time == january.observations[
        0
    ].open_time + timedelta(hours=1) - timedelta(microseconds=1)


def test_misaligned_open_and_wrong_close_time_are_errors(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(
        rows[0],
        open_time=rows[0].open_time + timedelta(microseconds=1),
        close_time=rows[0].close_time + timedelta(milliseconds=1),
    )

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.OPEN_TIME_NOT_HOURLY in _codes(report)
    assert IssueCode.CLOSE_TIME_MISMATCH in _codes(report)


def test_open_time_outside_requested_month_is_an_error(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(
        rows[0],
        open_time=rows[0].open_time - timedelta(hours=1),
        close_time=rows[0].close_time - timedelta(hours=1),
    )

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.OPEN_TIME_OUTSIDE_MONTH in _codes(report)


@pytest.mark.parametrize(
    "replacement",
    [
        {"low": Decimal(30001)},
        {"high": Decimal(29999)},
        {"low": Decimal(30002), "close": Decimal(30001)},
        {"high": Decimal(30000), "close": Decimal(30001)},
    ],
)
def test_every_ohlc_inequality_is_enforced(tmp_path, replacement):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(rows[0], **replacement)

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.OHLC_INCONSISTENT in _codes(report)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_every_price_field_must_be_positive(tmp_path, field):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(rows[0], **{field: Decimal(0)})

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.NONPOSITIVE_PRICE in _codes(report)


@pytest.mark.parametrize(
    "field",
    [
        "volume",
        "quote_asset_volume",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ],
)
def test_negative_volume_fields_are_errors(tmp_path, field):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(rows[0], **{field: Decimal(-1)})

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.NEGATIVE_VOLUME in _codes(report)


def test_trade_count_and_taker_volume_rules(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(
        rows[0],
        number_of_trades=-1,
        taker_buy_base_asset_volume=rows[0].volume + 1,
        taker_buy_quote_asset_volume=rows[0].quote_asset_volume + 1,
    )

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert IssueCode.NEGATIVE_TRADE_COUNT in _codes(report)
    assert (
        sum(
            issue.code is IssueCode.TAKER_VOLUME_EXCEEDS_TOTAL
            for issue in report.issues
        )
        == 2
    )


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1.0000000000000000001"),
        Decimal(100000000000000000000),
    ],
)
def test_nonfinite_and_unsupported_decimals_are_errors(tmp_path, value):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(rows[0], volume=value)

    report = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert {
        IssueCode.NONFINITE_DECIMAL,
        IssueCode.DECIMAL_PRECISION_EXCEEDED,
    } & _codes(report)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("99999999999999999999.999999999999999999"), True),
        (Decimal("0.000000000000000001"), True),
        (Decimal("1.0000000000000000000"), False),
        (Decimal("0E-19"), False),
        (Decimal(100000000000000000000), False),
    ],
)
def test_decimal_precision_and_scale_boundaries(value, expected):
    assert decimal_fits_storage(value) is expected


@pytest.mark.parametrize("field", ["archive_url", "dataset_terms_url"])
def test_source_metadata_mismatch_is_an_error(tmp_path, field):
    assessment = _assessment(tmp_path)
    provenance = replace(
        assessment.provenance,
        **{field: "https://example.invalid/forged"},
    )

    report = validate_month(
        assessment.request,
        assessment.observations,
        provenance,
    ).report

    assert IssueCode.SOURCE_METADATA_MISMATCH in _codes(report)


def test_warning_only_rows_are_valid_and_source_order_is_preserved(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows[0] = replace(
        rows[0],
        volume=Decimal(0),
        quote_asset_volume=Decimal(0),
        taker_buy_base_asset_volume=Decimal(0),
        taker_buy_quote_asset_volume=Decimal(0),
        number_of_trades=0,
        ignore=Decimal(1),
    )
    original = tuple(rows)

    checked = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    )

    assert checked.report.is_valid
    assert {issue.severity for issue in checked.report.issues} == {Severity.WARNING}
    assert {
        IssueCode.ZERO_VOLUME,
        IssueCode.ZERO_TRADES,
        IssueCode.IGNORE_NONZERO,
    } <= _codes(checked.report)
    assert checked.observations == original
    assert checked.observations[0] is original[0]


def test_validation_output_is_deterministic(tmp_path):
    assessment = _assessment(tmp_path)
    rows = list(assessment.observations)
    rows.pop(5)

    first = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report
    second = validate_month(
        assessment.request,
        rows,
        assessment.provenance,
    ).report

    assert first == second
    assert tuple(issue.code.value for issue in first.issues) == tuple(
        issue.code.value for issue in second.issues
    )


def test_missing_month_and_cross_month_gap_are_reported(tmp_path):
    january = _assessment(tmp_path / "january", 2024, 1)
    march = _assessment(tmp_path / "march", 2024, 3)

    missing = validate_month_sequence(
        (january, march),
        (YearMonth(2024, 1), YearMonth(2024, 2), YearMonth(2024, 3)),
    )

    assert IssueCode.MISSING_MONTH in _codes(missing)

    february = _assessment(tmp_path / "february", 2024, 2)
    shifted_rows = list(february.observations)
    shifted_rows[0] = replace(
        shifted_rows[0],
        open_time=shifted_rows[0].open_time + timedelta(hours=1),
        close_time=shifted_rows[0].close_time + timedelta(hours=1),
    )
    shifted_february = replace(february, observations=tuple(shifted_rows))

    continuity = validate_month_sequence(
        (january, shifted_february),
        (YearMonth(2024, 1), YearMonth(2024, 2)),
    )

    assert IssueCode.CROSS_MONTH_GAP in _codes(continuity)


def test_duplicate_and_out_of_order_months_are_reported(tmp_path):
    january = _assessment(tmp_path / "january", 2024, 1)
    february = _assessment(tmp_path / "february", 2024, 2)

    report = validate_month_sequence(
        (february, january, january),
        (YearMonth(2024, 1), YearMonth(2024, 2)),
    )

    assert IssueCode.OUT_OF_ORDER_MONTH in _codes(report)
    assert IssueCode.DUPLICATE_MONTH in _codes(report)
