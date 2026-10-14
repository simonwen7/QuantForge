"""Safe, streaming validation for Binance Vision monthly kline archives."""

from __future__ import annotations

import csv
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from io import TextIOWrapper
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from quantforge.ingestion.models import (
    ArchiveValidation,
    ArchiveValidationError,
    CsvValidationError,
    Kline,
    MonthlyKlineRequest,
    SafetyLimits,
    TimestampUnit,
    TimestampValidationError,
)

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
)


def _month_bounds(request: MonthlyKlineRequest) -> tuple[datetime, datetime]:
    start = datetime(
        request.period.year,
        request.period.month,
        1,
        tzinfo=UTC,
    )
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


def normalize_timestamp(
    raw_value: str,
    unit: TimestampUnit,
    request: MonthlyKlineRequest,
    field_name: str,
) -> datetime:
    """Convert an integer epoch timestamp to exact UTC without using floats."""
    try:
        raw_timestamp = int(raw_value)
    except ValueError as error:
        raise TimestampValidationError(
            f"{field_name} must be an integer timestamp: {raw_value!r}"
        ) from error
    if raw_timestamp < 0:
        raise TimestampValidationError(f"{field_name} cannot be negative")

    if unit is TimestampUnit.MILLISECONDS:
        divisor = 1_000
    elif unit is TimestampUnit.MICROSECONDS:
        divisor = 1_000_000
    else:
        raise TimestampValidationError(f"unsupported timestamp unit: {unit!r}")
    seconds, remainder = divmod(raw_timestamp, divisor)
    microseconds = (
        remainder * 1_000 if unit is TimestampUnit.MILLISECONDS else remainder
    )
    try:
        normalized = datetime.fromtimestamp(seconds, tz=UTC).replace(
            microsecond=microseconds
        )
    except (OverflowError, OSError, ValueError) as error:
        raise TimestampValidationError(
            f"{field_name} is outside the supported datetime range when interpreted "
            f"as {unit.value}: {raw_value!r}"
        ) from error

    month_start, next_month_start = _month_bounds(request)
    if not month_start <= normalized < next_month_start:
        raise TimestampValidationError(
            f"{field_name} does not fall within "
            f"{request.period.year:04d}-{request.period.month:02d} when interpreted "
            f"as {unit.value}: {raw_value!r}"
        )
    return normalized


def _parse_decimal(value: str, field_name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise CsvValidationError(
            f"{field_name} must be a decimal value: {value!r}"
        ) from error
    if not parsed.is_finite():
        raise CsvValidationError(f"{field_name} must be finite: {value!r}")
    return parsed


def _parse_kline(
    row: list[str],
    request: MonthlyKlineRequest,
    row_number: int,
) -> Kline:
    if len(row) != len(KLINE_COLUMNS):
        raise CsvValidationError(
            f"CSV row {row_number} has {len(row)} columns; "
            f"expected {len(KLINE_COLUMNS)}"
        )

    unit = request.timestamp_unit
    open_time = normalize_timestamp(row[0], unit, request, "open_time")
    close_time = normalize_timestamp(row[6], unit, request, "close_time")
    if close_time <= open_time:
        raise TimestampValidationError(
            f"CSV row {row_number} close time must follow its open time"
        )

    try:
        number_of_trades = int(row[8])
    except ValueError as error:
        raise CsvValidationError(
            f"number_of_trades must be an integer on CSV row {row_number}: {row[8]!r}"
        ) from error
    if number_of_trades < 0:
        raise CsvValidationError(
            f"number_of_trades cannot be negative on CSV row {row_number}"
        )

    return Kline(
        open_time=open_time,
        open=_parse_decimal(row[1], "open"),
        high=_parse_decimal(row[2], "high"),
        low=_parse_decimal(row[3], "low"),
        close=_parse_decimal(row[4], "close"),
        volume=_parse_decimal(row[5], "volume"),
        close_time=close_time,
        quote_asset_volume=_parse_decimal(row[7], "quote_asset_volume"),
        number_of_trades=number_of_trades,
        taker_buy_base_asset_volume=_parse_decimal(
            row[9], "taker_buy_base_asset_volume"
        ),
        taker_buy_quote_asset_volume=_parse_decimal(
            row[10], "taker_buy_quote_asset_volume"
        ),
        ignore=_parse_decimal(row[11], "ignore"),
    )


def _validate_member(
    info: ZipInfo,
    request: MonthlyKlineRequest,
    limits: SafetyLimits,
) -> None:
    member_path = PurePosixPath(info.filename)
    if (
        member_path.is_absolute()
        or ".." in member_path.parts
        or "\\" in info.filename
        or "\x00" in info.filename
    ):
        raise ArchiveValidationError(f"unsafe ZIP member path: {info.filename!r}")
    if info.is_dir():
        raise ArchiveValidationError(
            f"unexpected directory in ZIP archive: {info.filename!r}"
        )

    unix_mode = info.external_attr >> 16
    if stat.S_ISLNK(unix_mode):
        raise ArchiveValidationError(
            f"symbolic links are not permitted in ZIP archives: {info.filename!r}"
        )
    if info.flag_bits & 0x1:
        raise ArchiveValidationError(
            f"encrypted ZIP members are not supported: {info.filename!r}"
        )
    if info.filename != request.csv_filename:
        raise ArchiveValidationError(
            f"unexpected ZIP member {info.filename!r}; "
            f"expected {request.csv_filename!r}"
        )
    if info.file_size > limits.max_uncompressed_bytes:
        raise ArchiveValidationError(
            f"ZIP member exceeds the {limits.max_uncompressed_bytes}-byte "
            "uncompressed safety limit"
        )
    if info.file_size > 0 and info.compress_size == 0:
        raise ArchiveValidationError(
            "non-empty ZIP member reports a zero compressed size"
        )
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > limits.max_compression_ratio:
            raise ArchiveValidationError(
                f"ZIP compression ratio {ratio:.1f} exceeds the "
                f"{limits.max_compression_ratio:.1f} safety limit"
            )


def iter_klines(
    archive_path: Path,
    request: MonthlyKlineRequest,
    limits: SafetyLimits | None = None,
) -> Iterator[Kline]:
    """Yield validated klines directly from a ZIP member without extraction."""
    active_limits = limits or SafetyLimits()
    try:
        archive_size = archive_path.stat().st_size
    except OSError as error:
        raise ArchiveValidationError(
            f"cannot inspect archive {archive_path}: {error}"
        ) from error
    if archive_size <= 0:
        raise ArchiveValidationError("archive is empty")
    if archive_size > active_limits.max_archive_bytes:
        raise ArchiveValidationError(
            f"archive exceeds the {active_limits.max_archive_bytes}-byte "
            "compressed safety limit"
        )

    try:
        with ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise ArchiveValidationError(
                    f"archive must contain exactly one CSV member; found {len(members)}"
                )
            member = members[0]
            _validate_member(member, request, active_limits)

            with (
                archive.open(member, "r") as raw_csv,
                TextIOWrapper(
                    raw_csv,
                    encoding="utf-8",
                    errors="strict",
                    newline="",
                ) as text_csv,
            ):
                reader = csv.reader(text_csv, strict=True)
                data_rows = 0
                for source_row_number, row in enumerate(reader, start=1):
                    if source_row_number == 1 and tuple(row) == KLINE_COLUMNS:
                        continue
                    if source_row_number == 1 and row and not row[0].isdigit():
                        raise CsvValidationError(
                            "CSV contains an unrecognized header or schema"
                        )
                    data_rows += 1
                    if data_rows > active_limits.max_csv_rows:
                        raise CsvValidationError(
                            f"CSV exceeds the {active_limits.max_csv_rows}-row "
                            "safety limit"
                        )
                    yield _parse_kline(row, request, source_row_number)
                if data_rows == 0:
                    raise CsvValidationError("CSV contains no kline rows")
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ArchiveValidationError(f"invalid ZIP archive: {error}") from error
    except UnicodeDecodeError as error:
        raise CsvValidationError("CSV is not valid UTF-8") from error
    except csv.Error as error:
        raise CsvValidationError(f"malformed CSV: {error}") from error


def validate_kline_archive(
    archive_path: Path,
    request: MonthlyKlineRequest,
    limits: SafetyLimits | None = None,
) -> ArchiveValidation:
    """Stream and validate every row, returning a compact validation summary."""
    row_count = sum(1 for _ in iter_klines(archive_path, request, limits))
    return ArchiveValidation(
        member_name=request.csv_filename,
        row_count=row_count,
        timestamp_unit=request.timestamp_unit,
    )
