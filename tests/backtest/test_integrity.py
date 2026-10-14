"""Verified loading, holdout refusal, and replay guards."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quantforge.backtest import BacktestEngine
from quantforge.ingestion import YearMonth, archive_url, checksum_url
from quantforge.ingestion.models import (
    DATASET_TERMS_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    IngestionResult,
    ProvenanceRecord,
)
from quantforge.research import (
    HoldoutAccessError,
    MarketBarLoader,
    ResearchAccessMode,
)
from quantforge.research.market_bars import DataOrigin, MarketBar, MarketBarDataset
from quantforge.storage import DuckDBParquetStore, SourceVerificationError
from tests.research.support import research_configuration
from tests.support.synthetic_klines import (
    FIXED_RETRIEVAL_TIME,
    make_klines,
    make_request,
    write_synthetic_source,
)


def _dummy_result(request) -> IngestionResult:
    digest = "b" * 64
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
        archive_size=1,
        retrieved_at_utc=FIXED_RETRIEVAL_TIME,
        source_timestamp_unit=request.timestamp_unit,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        dataset_terms_url=DATASET_TERMS_URL,
        archive_member_name=request.csv_filename,
    )
    return IngestionResult(
        archive_path=Path("unused.zip"),
        provenance_path=Path("unused.provenance.json"),
        provenance=provenance,
        reused_existing_archive=False,
    )


def test_loader_preserves_open_decimals_and_does_not_use_the_network(
    tmp_path, monkeypatch
):
    import urllib.request

    request = make_request(2024, 1)
    rows = make_klines(request)
    ingestion = write_synthetic_source(tmp_path / "raw", request, rows)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network access")),
    )

    dataset = MarketBarLoader(DuckDBParquetStore(tmp_path / "processed")).load(
        [(request, ingestion)],
        research_configuration((request.period,)),
    )

    assert dataset.origin is DataOrigin.VERIFIED_STORAGE
    assert dataset.bars[0].open == rows[0].open
    assert dataset.bars[0].close == rows[0].close
    assert isinstance(dataset.bars[0].open, Decimal)
    assert dataset.bars[0].available_at == dataset.bars[0].open_time + __import__(
        "datetime"
    ).timedelta(hours=1)
    assert (
        dataset.lineage[0].source_archive_sha256 == ingestion.provenance.actual_sha256
    )
    assert dataset.bars[0].origin is DataOrigin.VERIFIED_STORAGE


def test_development_loader_rejects_holdout_before_storage(tmp_path, monkeypatch):
    request = make_request(2025, 1)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("holdout storage was materialized")
        ),
    )

    with pytest.raises(HoldoutAccessError, match="development"):
        MarketBarLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,)),
        )


def test_tampered_archive_is_rejected(tmp_path):
    request = make_request(2024, 3)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    ingestion.archive_path.write_bytes(
        ingestion.archive_path.read_bytes() + b"tampered"
    )

    with pytest.raises(SourceVerificationError):
        MarketBarLoader(DuckDBParquetStore(tmp_path / "processed")).load(
            [(request, ingestion)],
            research_configuration((request.period,)),
        )


def test_engine_refuses_final_evaluation_without_reading_storage():
    when = datetime(2024, 6, 1, tzinfo=UTC)
    bar = MarketBar(
        open_time=when,
        close_time=when
        + __import__("datetime").timedelta(hours=1)
        - __import__("datetime").timedelta(milliseconds=1),
        open=Decimal(10),
        high=Decimal(10),
        low=Decimal(10),
        close=Decimal(10),
        volume=Decimal(1),
        source_archive_sha256="a" * 64,
        source_year=2024,
        source_month=6,
        source_schema_version="binance-spot-kline-12-v1",
        origin=DataOrigin.VERIFIED_STORAGE,
        available_at=when + __import__("datetime").timedelta(hours=1),
    )
    dataset = MarketBarDataset(
        bars=(bar,),
        expected_periods=(YearMonth(2024, 6),),
        covered_periods=(YearMonth(2024, 6),),
        gaps=(),
        lineage=(),
        access_mode=ResearchAccessMode.FINAL_EVALUATION,
        coverage_policy=research_configuration((YearMonth(2024, 6),)).coverage_policy,
        final_evaluation_authorized=True,
        is_complete=True,
        origin=DataOrigin.VERIFIED_STORAGE,
    )

    with pytest.raises(HoldoutAccessError, match="final holdout"):
        BacktestEngine().run_verified(dataset, ())


def test_synthetic_helper_is_not_labeled_verified():
    bar = __import__(
        "quantforge.research", fromlist=["synthetic_market_bar"]
    ).synthetic_market_bar(
        datetime(2024, 6, 1, tzinfo=UTC),
        "10",
    )

    assert bar.origin is DataOrigin.SYNTHETIC_UNVERIFIED
    assert bar.source_schema_version == "synthetic-unverified"
