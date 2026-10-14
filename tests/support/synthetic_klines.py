"""Deterministic synthetic Binance-like source data for offline tests."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from quantforge.ingestion import (
    IngestionResult,
    Kline,
    MonthlyKlineRequest,
    TimestampUnit,
    YearMonth,
    archive_url,
    checksum_url,
    serialize_provenance,
)
from quantforge.ingestion.models import (
    DATASET_TERMS_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    ProvenanceRecord,
)
from quantforge.quality import expected_hourly_rows

FIXED_RETRIEVAL_TIME = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


def make_request(year: int = 2024, month: int = 1) -> MonthlyKlineRequest:
    """Build one supported request."""
    return MonthlyKlineRequest(YearMonth(year, month))


def make_klines(request: MonthlyKlineRequest) -> tuple[Kline, ...]:
    """Build a complete, valid monthly sequence with exact Decimal values."""
    start = datetime(
        request.period.year,
        request.period.month,
        1,
        tzinfo=UTC,
    )
    close_tick = (
        timedelta(milliseconds=1)
        if request.timestamp_unit is TimestampUnit.MILLISECONDS
        else timedelta(microseconds=1)
    )
    rows: list[Kline] = []
    for offset in range(expected_hourly_rows(request.period)):
        open_time = start + timedelta(hours=offset)
        base = Decimal(30000) + Decimal(offset)
        rows.append(
            Kline(
                open_time=open_time,
                open=base,
                high=base + Decimal(2),
                low=base - Decimal(2),
                close=base + Decimal(1),
                volume=Decimal("12.123456789012345678"),
                close_time=open_time + timedelta(hours=1) - close_tick,
                quote_asset_volume=Decimal("363703.703670370370340000"),
                number_of_trades=100 + offset,
                taker_buy_base_asset_volume=Decimal("6.000000000000000001"),
                taker_buy_quote_asset_volume=Decimal("180000.000000000000000001"),
                ignore=Decimal(0),
            )
        )
    return tuple(rows)


def _timestamp_integer(value: datetime, unit: TimestampUnit) -> int:
    elapsed = value - datetime(1970, 1, 1, tzinfo=UTC)
    elapsed_microseconds = (
        elapsed.days * 86_400_000_000
        + elapsed.seconds * 1_000_000
        + elapsed.microseconds
    )
    if unit is TimestampUnit.MILLISECONDS:
        if elapsed_microseconds % 1_000:
            raise ValueError("millisecond timestamp is not exactly representable")
        return elapsed_microseconds // 1_000
    return elapsed_microseconds


def _csv_bytes(
    request: MonthlyKlineRequest,
    observations: tuple[Kline, ...],
) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    for row in observations:
        writer.writerow(
            (
                _timestamp_integer(row.open_time, request.timestamp_unit),
                str(row.open),
                str(row.high),
                str(row.low),
                str(row.close),
                str(row.volume),
                _timestamp_integer(row.close_time, request.timestamp_unit),
                str(row.quote_asset_volume),
                row.number_of_trades,
                str(row.taker_buy_base_asset_volume),
                str(row.taker_buy_quote_asset_volume),
                str(row.ignore),
            )
        )
    return text.getvalue().encode("utf-8")


def write_synthetic_source(
    root: Path,
    request: MonthlyKlineRequest,
    observations: tuple[Kline, ...] | None = None,
) -> IngestionResult:
    """Write a valid raw ZIP and provenance pair without network access."""
    rows = observations if observations is not None else make_klines(request)
    archive_path = (
        root
        / "binance-vision"
        / request.market
        / "klines"
        / request.symbol
        / request.interval
        / f"{request.period.year:04d}"
        / request.archive_filename
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(request.csv_filename, _csv_bytes(request, rows))

    archive_bytes = archive_path.read_bytes()
    digest = hashlib.sha256(archive_bytes).hexdigest()
    provenance = ProvenanceRecord(
        source_name=SOURCE_NAME,
        archive_url=archive_url(request),
        checksum_url=checksum_url(request),
        symbol=request.symbol,
        market=request.market,
        interval=request.interval,
        year=request.period.year,
        month=request.period.month,
        expected_sha256=digest,
        actual_sha256=digest,
        archive_size=len(archive_bytes),
        retrieved_at_utc=FIXED_RETRIEVAL_TIME,
        source_timestamp_unit=request.timestamp_unit,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        dataset_terms_url=DATASET_TERMS_URL,
        archive_member_name=request.csv_filename,
        archive_http_metadata={"etag": '"synthetic-etag"'},
        checksum_http_metadata={"last-modified": "synthetic"},
    )
    provenance_path = archive_path.with_suffix(".provenance.json")
    provenance_path.write_text(
        serialize_provenance(provenance),
        encoding="utf-8",
        newline="\n",
    )
    return IngestionResult(
        archive_path=archive_path,
        provenance_path=provenance_path,
        provenance=provenance,
        reused_existing_archive=False,
    )
