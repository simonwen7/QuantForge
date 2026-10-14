"""Offline integration tests for exact DuckDB/Parquet storage."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import duckdb
import pytest

from quantforge.storage import (
    PARQUET_SCHEMA,
    DuckDBParquetStore,
    read_manifest,
)
from tests.support.synthetic_klines import (
    make_klines,
    make_request,
    write_synthetic_source,
)

FIXED_STORAGE_TIME = datetime(2026, 2, 3, 4, 5, 6, 789012, tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _epoch_us(value):
    delta = value - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _read_rows(parquet_path):
    with duckdb.connect(":memory:") as connection:
        connection.execute("SET TimeZone = 'UTC'")
        return connection.execute(
            """
            SELECT
                symbol,
                market,
                interval,
                epoch_us(open_time),
                open,
                high,
                low,
                close,
                volume,
                epoch_us(close_time)
            FROM read_parquet(?, hive_partitioning = false)
            ORDER BY open_time
            """,
            [str(parquet_path)],
        ).fetchall()


def test_exact_decimal_and_microsecond_timestamp_round_trip(tmp_path):
    request = make_request(2025, 1)
    rows = list(make_klines(request))
    rows[0] = replace(
        rows[0],
        volume=Decimal("0.000000000000000001"),
        quote_asset_volume=Decimal("0.000000000000000001"),
        taker_buy_base_asset_volume=Decimal("0.000000000000000001"),
        taker_buy_quote_asset_volume=Decimal("0.000000000000000001"),
    )
    ingestion = write_synthetic_source(
        tmp_path / "raw",
        request,
        tuple(rows),
    )
    store = DuckDBParquetStore(
        tmp_path / "processed",
        clock=lambda: FIXED_STORAGE_TIME,
    )

    result = store.process_month(request, ingestion)
    stored = _read_rows(result.parquet_path)

    assert stored[0][8] == Decimal("1E-18")
    assert stored[0][9] == _epoch_us(rows[0].close_time)
    assert stored[-1][3] == _epoch_us(rows[-1].open_time)


def test_parquet_schema_sql_queries_and_manifest_are_exact(tmp_path):
    request = make_request(2024, 4)
    rows = make_klines(request)
    ingestion = write_synthetic_source(tmp_path / "raw", request, rows)
    result = DuckDBParquetStore(
        tmp_path / "processed",
        clock=lambda: FIXED_STORAGE_TIME,
    ).process_month(request, ingestion)

    with duckdb.connect(":memory:") as connection:
        connection.execute("SET TimeZone = 'UTC'")
        schema = tuple(
            (row[0], row[1])
            for row in connection.execute(
                """
                DESCRIBE SELECT *
                FROM read_parquet(?, hive_partitioning = false)
                """,
                [str(result.parquet_path)],
            ).fetchall()
        )
        aggregates = connection.execute(
            """
            SELECT
                count(*),
                min(open),
                max(open),
                sum(volume),
                count(DISTINCT open_time)
            FROM read_parquet(?, hive_partitioning = false)
            """,
            [str(result.parquet_path)],
        ).fetchone()

    assert schema == PARQUET_SCHEMA
    assert aggregates == (
        len(rows),
        rows[0].open,
        rows[-1].open,
        sum((row.volume for row in rows), Decimal(0)),
        len(rows),
    )
    assert result.partition_path == (
        tmp_path
        / "processed"
        / "binance-vision"
        / "spot"
        / "klines"
        / "symbol=BTCUSDT"
        / "interval=1h"
        / "year=2024"
        / "month=04"
    )
    manifest = read_manifest(result.manifest_path)
    assert manifest == result.manifest
    assert manifest.created_at_utc == FIXED_STORAGE_TIME
    assert manifest.observation_count == 720
    assert manifest.source_archive_sha256 == ingestion.provenance.actual_sha256
    assert manifest.parquet_size == result.parquet_path.stat().st_size


def test_documented_query_is_timezone_independent_without_timestamp_conversion(
    tmp_path,
):
    request = make_request(2025, 1)
    rows = make_klines(request)
    ingestion = write_synthetic_source(tmp_path / "raw", request, rows)
    result = DuckDBParquetStore(tmp_path / "processed").process_month(
        request,
        ingestion,
    )

    def query(timezone):
        with duckdb.connect(":memory:") as connection:
            connection.execute(f"SET TimeZone = '{timezone}'")
            return connection.execute(
                """
                SELECT
                    count(*),
                    epoch_us(min(open_time)),
                    epoch_us(max(open_time)),
                    sum(volume)
                FROM read_parquet(?, hive_partitioning = false)
                """,
                [str(result.parquet_path)],
            ).fetchone()

    expected = (
        len(rows),
        _epoch_us(rows[0].open_time),
        _epoch_us(rows[-1].open_time),
        sum((row.volume for row in rows), Decimal(0)),
    )
    assert query("UTC") == expected
    assert query("America/New_York") == expected


def test_warning_only_month_is_published_with_issues_preserved(tmp_path):
    request = make_request(2024, 2)
    rows = list(make_klines(request))
    rows[0] = replace(
        rows[0],
        volume=Decimal(0),
        quote_asset_volume=Decimal(0),
        taker_buy_base_asset_volume=Decimal(0),
        taker_buy_quote_asset_volume=Decimal(0),
        number_of_trades=0,
        ignore=Decimal(1),
    )
    ingestion = write_synthetic_source(
        tmp_path / "raw",
        request,
        tuple(rows),
    )

    result = DuckDBParquetStore(tmp_path / "processed").process_month(
        request,
        ingestion,
    )
    validation = json.loads(result.validation_path.read_text(encoding="utf-8"))

    assert validation["error_count"] == 0
    assert validation["warning_count"] == 3
    assert result.manifest.warning_count == 3
    assert {issue["code"] for issue in validation["issues"]} == {
        "zero_volume",
        "zero_trades",
        "ignore_nonzero",
    }


def test_reprocessing_same_verified_source_reuses_partition(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(
        tmp_path / "processed",
        clock=lambda: FIXED_STORAGE_TIME,
    )

    first = store.process_month(request, ingestion)
    second = store.process_month(request, ingestion)

    assert not first.reused_existing_partition
    assert second.reused_existing_partition
    assert second.manifest == first.manifest
    assert second.parquet_path.read_bytes() == first.parquet_path.read_bytes()


def test_decimal_aggregate_overflow_is_explicit(tmp_path):
    request = make_request(2024, 2)
    maximum_integer = Decimal(99999999999999999999)
    rows = tuple(
        replace(
            row,
            volume=maximum_integer,
            taker_buy_base_asset_volume=Decimal(0),
        )
        for row in make_klines(request)
    )
    ingestion = write_synthetic_source(tmp_path / "raw", request, rows)
    result = DuckDBParquetStore(tmp_path / "processed").process_month(
        request,
        ingestion,
    )

    with (
        duckdb.connect(":memory:") as connection,
        pytest.raises(duckdb.Error, match="Overflow"),
    ):
        connection.execute(
            """
            SELECT sum(volume)
            FROM read_parquet(?, hive_partitioning = false)
            """,
            [str(result.parquet_path)],
        ).fetchone()
