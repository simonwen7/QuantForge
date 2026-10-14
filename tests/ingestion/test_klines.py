from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from quantforge.ingestion.klines import (
    KLINE_COLUMNS,
    iter_klines,
    validate_kline_archive,
)
from quantforge.ingestion.models import (
    ArchiveValidationError,
    CsvValidationError,
    MonthlyKlineRequest,
    SafetyLimits,
    TimestampUnit,
    TimestampValidationError,
    YearMonth,
)

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_value(value: datetime, unit: TimestampUnit) -> int:
    delta = value - EPOCH
    total_microseconds = (
        delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    if unit is TimestampUnit.MILLISECONDS:
        return total_microseconds // 1_000
    return total_microseconds


def _synthetic_row(
    request: MonthlyKlineRequest,
    *,
    open_time: datetime | None = None,
    close_time: datetime | None = None,
) -> list[str]:
    unit = request.timestamp_unit
    opened = open_time or datetime(
        request.period.year,
        request.period.month,
        1,
        tzinfo=UTC,
    )
    closed = close_time or (
        opened
        + timedelta(hours=1)
        - (
            timedelta(milliseconds=1)
            if unit is TimestampUnit.MILLISECONDS
            else timedelta(microseconds=1)
        )
    )
    return [
        str(_epoch_value(opened, unit)),
        "100.10",
        "102.20",
        "99.90",
        "101.30",
        "12.50",
        str(_epoch_value(closed, unit)),
        "1266.25",
        "42",
        "7.25",
        "734.425",
        "0",
    ]


def _write_zip(
    path: Path,
    member_name: str,
    content: bytes,
    *,
    compression: int = ZIP_STORED,
) -> None:
    with ZipFile(path, "w", compression=compression) as archive:
        archive.writestr(member_name, content)


def _csv_bytes(*rows: list[str]) -> bytes:
    return ("\n".join(",".join(row) for row in rows) + "\n").encode()


def _hourly_rows(
    request: MonthlyKlineRequest,
    row_count: int,
) -> list[list[str]]:
    month_start = datetime(
        request.period.year,
        request.period.month,
        1,
        tzinfo=UTC,
    )
    return [
        _synthetic_row(
            request,
            open_time=month_start + timedelta(hours=offset),
        )
        for offset in range(row_count)
    ]


def _mark_zip_encrypted(path: Path) -> None:
    content = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        header = content.index(signature)
        flags_at = header + flag_offset
        flags = int.from_bytes(content[flags_at : flags_at + 2], "little")
        content[flags_at : flags_at + 2] = (flags | 0x1).to_bytes(2, "little")
    path.write_bytes(content)


def test_parses_2024_millisecond_timestamps_exactly(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(_synthetic_row(request)))

    [kline] = list(iter_klines(archive, request))

    assert kline.open_time == datetime(2024, 1, 1, tzinfo=UTC)
    assert kline.close_time == datetime(
        2024,
        1,
        1,
        0,
        59,
        59,
        999_000,
        tzinfo=UTC,
    )
    assert kline.open == Decimal("100.10")


def test_preserves_2025_microsecond_precision(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2025, 1))
    opened = datetime(2025, 1, 1, 0, 0, 0, 123_456, tzinfo=UTC)
    closed = datetime(2025, 1, 1, 0, 59, 59, 999_999, tzinfo=UTC)
    archive = tmp_path / request.archive_filename
    row = _synthetic_row(request, open_time=opened, close_time=closed)
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    [kline] = list(iter_klines(archive, request))

    assert kline.open_time == opened
    assert kline.close_time == closed


def test_december_2024_to_january_2025_unit_transition(tmp_path: Path) -> None:
    december = MonthlyKlineRequest(YearMonth(2024, 12))
    december_archive = tmp_path / december.archive_filename
    december_open = datetime(2024, 12, 31, 23, tzinfo=UTC)
    _write_zip(
        december_archive,
        december.csv_filename,
        _csv_bytes(_synthetic_row(december, open_time=december_open)),
    )

    january = MonthlyKlineRequest(YearMonth(2025, 1))
    january_archive = tmp_path / january.archive_filename
    january_open = datetime(2025, 1, 1, 0, 0, 0, 654_321, tzinfo=UTC)
    _write_zip(
        january_archive,
        january.csv_filename,
        _csv_bytes(_synthetic_row(january, open_time=january_open)),
    )

    [december_kline] = list(iter_klines(december_archive, december))
    [january_kline] = list(iter_klines(january_archive, january))

    assert december_kline.open_time == december_open
    assert december_kline.open_time.microsecond == 0
    assert january_kline.open_time == january_open
    assert january_kline.open_time.microsecond == 654_321


def test_rejects_timestamp_in_wrong_unit(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2025, 1))
    row = _synthetic_row(request)
    row[0] = str(
        _epoch_value(datetime(2025, 1, 1, tzinfo=UTC), TimestampUnit.MILLISECONDS)
    )
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    with pytest.raises(TimestampValidationError, match="microseconds"):
        list(iter_klines(archive, request))


def test_rejects_2024_timestamp_in_microseconds(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 12))
    row = _synthetic_row(request)
    row[0] = str(
        _epoch_value(
            datetime(2024, 12, 1, tzinfo=UTC),
            TimestampUnit.MICROSECONDS,
        )
    )
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    with pytest.raises(TimestampValidationError, match="milliseconds"):
        list(iter_klines(archive, request))


@pytest.mark.parametrize(
    "outside_month",
    [
        datetime(2024, 1, 31, 23, tzinfo=UTC),
        datetime(2024, 3, 1, tzinfo=UTC),
    ],
)
def test_rejects_timestamps_outside_requested_month(
    tmp_path: Path,
    outside_month: datetime,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 2))
    row = _synthetic_row(request)
    row[0] = str(_epoch_value(outside_month, request.timestamp_unit))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    with pytest.raises(TimestampValidationError, match="does not fall within"):
        list(iter_klines(archive, request))


def test_rejects_close_before_open(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    opened = datetime(2024, 1, 1, 1, tzinfo=UTC)
    closed = datetime(2024, 1, 1, 0, tzinfo=UTC)
    archive = tmp_path / request.archive_filename
    row = _synthetic_row(request, open_time=opened, close_time=closed)
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    with pytest.raises(TimestampValidationError, match="must follow"):
        list(iter_klines(archive, request))


def test_rejects_wrong_column_count(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 2))
    archive = tmp_path / request.archive_filename
    _write_zip(
        archive,
        request.csv_filename,
        _csv_bytes(_synthetic_row(request)[:-1]),
    )

    with pytest.raises(CsvValidationError, match="11 columns"):
        list(iter_klines(archive, request))


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (1, "not-a-decimal", "open must be a decimal"),
        (1, "NaN", "open must be finite"),
        (8, "1.5", "number_of_trades must be an integer"),
        (8, "-1", "number_of_trades cannot be negative"),
    ],
)
def test_rejects_invalid_numeric_values(
    tmp_path: Path,
    column: int,
    value: str,
    message: str,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 3))
    row = _synthetic_row(request)
    row[column] = value
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(row))

    with pytest.raises(CsvValidationError, match=message):
        list(iter_klines(archive, request))


def test_accepts_only_the_recognized_header(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 4))
    archive = tmp_path / request.archive_filename
    _write_zip(
        archive,
        request.csv_filename,
        _csv_bytes(list(KLINE_COLUMNS), _synthetic_row(request)),
    )

    assert len(list(iter_klines(archive, request))) == 1

    unknown_header = list(KLINE_COLUMNS)
    unknown_header[0] = "timestamp"
    _write_zip(
        archive,
        request.csv_filename,
        _csv_bytes(unknown_header, _synthetic_row(request)),
    )
    with pytest.raises(CsvValidationError, match="unrecognized header"):
        list(iter_klines(archive, request))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff,\xfe\n", "valid UTF-8"),
        (b'"unterminated,field\n', "malformed CSV"),
    ],
)
def test_rejects_invalid_text_and_malformed_csv(
    tmp_path: Path,
    content: bytes,
    message: str,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 5))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, content)

    with pytest.raises(CsvValidationError, match=message):
        list(iter_klines(archive, request))


def test_rejects_corrupted_zip(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 6))
    archive = tmp_path / request.archive_filename
    archive.write_bytes(b"not a zip archive")

    with pytest.raises(ArchiveValidationError, match="invalid ZIP"):
        list(iter_klines(archive, request))


def test_rejects_truncated_zip(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 6))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(_synthetic_row(request)))
    archive.write_bytes(archive.read_bytes()[:-12])

    with pytest.raises(ArchiveValidationError, match="invalid ZIP"):
        list(iter_klines(archive, request))


def test_rejects_unsafe_zip_member(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 7))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, "../escape.csv", _csv_bytes(_synthetic_row(request)))

    with pytest.raises(ArchiveValidationError, match="unsafe ZIP member"):
        list(iter_klines(archive, request))


def test_rejects_symbolic_link_member(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 7))
    archive_path = tmp_path / request.archive_filename
    member = ZipInfo(request.csv_filename)
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(member, b"synthetic-target")

    with pytest.raises(ArchiveValidationError, match="symbolic links"):
        list(iter_klines(archive_path, request))


def test_rejects_unexpected_and_multiple_members(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 7))
    unexpected = tmp_path / "unexpected.zip"
    _write_zip(
        unexpected,
        "unexpected.csv",
        _csv_bytes(_synthetic_row(request)),
    )
    with pytest.raises(ArchiveValidationError, match="unexpected ZIP member"):
        list(iter_klines(unexpected, request))

    multiple = tmp_path / "multiple.zip"
    with ZipFile(multiple, "w") as archive:
        archive.writestr(
            request.csv_filename,
            _csv_bytes(_synthetic_row(request)),
        )
        archive.writestr("extra.csv", b"synthetic")
    with pytest.raises(ArchiveValidationError, match="exactly one"):
        list(iter_klines(multiple, request))


def test_rejects_encrypted_member(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 7))
    archive = tmp_path / request.archive_filename
    _write_zip(archive, request.csv_filename, _csv_bytes(_synthetic_row(request)))
    _mark_zip_encrypted(archive)

    with pytest.raises(ArchiveValidationError, match="encrypted"):
        list(iter_klines(archive, request))


def test_rejects_empty_archive_and_empty_csv(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 7))
    empty_archive = tmp_path / "empty-archive.zip"
    with ZipFile(empty_archive, "w"):
        pass
    with pytest.raises(ArchiveValidationError, match="exactly one"):
        list(iter_klines(empty_archive, request))

    empty_csv = tmp_path / "empty-csv.zip"
    _write_zip(empty_csv, request.csv_filename, b"")
    with pytest.raises(CsvValidationError, match="no kline rows"):
        list(iter_klines(empty_csv, request))


def test_rejects_excessive_archive_expansion(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 8))
    archive = tmp_path / request.archive_filename
    content = _csv_bytes(*([_synthetic_row(request)] * 100))
    _write_zip(
        archive,
        request.csv_filename,
        content,
        compression=ZIP_DEFLATED,
    )
    limits = SafetyLimits(max_compression_ratio=1.0)

    with pytest.raises(ArchiveValidationError, match="compression ratio"):
        list(iter_klines(archive, request, limits))


def test_rejects_compressed_and_uncompressed_size_limit_violations(
    tmp_path: Path,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 8))
    archive = tmp_path / request.archive_filename
    content = _csv_bytes(_synthetic_row(request))
    _write_zip(archive, request.csv_filename, content)

    with pytest.raises(ArchiveValidationError, match="compressed safety limit"):
        list(iter_klines(archive, request, SafetyLimits(max_archive_bytes=1)))
    with pytest.raises(ArchiveValidationError, match="uncompressed safety limit"):
        list(
            iter_klines(
                archive,
                request,
                SafetyLimits(max_uncompressed_bytes=1),
            )
        )


@pytest.mark.parametrize(
    ("year", "month", "row_count"),
    [
        (2023, 2, 672),
        (2024, 4, 720),
        (2024, 1, 744),
    ],
)
def test_accepts_representative_complete_month_row_counts(
    tmp_path: Path,
    year: int,
    month: int,
    row_count: int,
) -> None:
    request = MonthlyKlineRequest(YearMonth(year, month))
    archive = tmp_path / request.archive_filename
    _write_zip(
        archive,
        request.csv_filename,
        _csv_bytes(*_hourly_rows(request, row_count)),
        compression=ZIP_DEFLATED,
    )

    validation = validate_kline_archive(archive, request)

    assert validation.row_count == row_count
    assert row_count < SafetyLimits().max_csv_rows
