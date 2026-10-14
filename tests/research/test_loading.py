"""Verified research loading tests using synthetic M2/M3 sources."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quantforge.ingestion import YearMonth, archive_url, checksum_url
from quantforge.ingestion.models import (
    DATASET_TERMS_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    IngestionResult,
    ProvenanceRecord,
)
from quantforge.quality import IssueCode
from quantforge.research import (
    CoverageError,
    CoveragePolicy,
    HoldoutAccessError,
    ResearchAccessMode,
    ResearchDataLoader,
)
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


def test_verified_month_preserves_decimal_availability_and_warnings(
    tmp_path,
    monkeypatch,
):
    import urllib.request
    from dataclasses import replace

    request = make_request(2024, 1)
    rows = list(make_klines(request))
    rows[0] = replace(
        rows[0],
        volume=Decimal(0),
        quote_asset_volume=Decimal(0),
        taker_buy_base_asset_volume=Decimal(0),
        taker_buy_quote_asset_volume=Decimal(0),
        number_of_trades=0,
    )
    ingestion = write_synthetic_source(tmp_path / "raw", request, tuple(rows))
    loader = ResearchDataLoader(DuckDBParquetStore(tmp_path / "processed"))
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network access")),
    )

    dataset = loader.load(
        [(request, ingestion)],
        research_configuration((request.period,)),
    )
    first = dataset.observations[0]
    deltas = {
        current.open_time - previous.open_time
        for previous, current in zip(dataset.observations, dataset.observations[1:])
    }

    assert dataset.is_complete
    assert not dataset.covers_approved_history
    assert len(dataset.observations) == 744
    assert first.close == rows[0].close
    assert isinstance(first.close, Decimal)
    assert first.available_at == first.open_time + timedelta(hours=1)
    assert first.close_time == first.available_at - timedelta(milliseconds=1)
    assert first.close_time.microsecond == 999_000
    assert deltas == {timedelta(hours=1)}
    assert (
        dataset.lineage[0].source_archive_sha256 == ingestion.provenance.actual_sha256
    )
    assert IssueCode.ZERO_VOLUME in {issue.code for issue in dataset.warnings}


def test_2025_availability_uses_hour_boundary_rather_than_close_time(tmp_path):
    request = make_request(2025, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    dataset = ResearchDataLoader(DuckDBParquetStore(tmp_path / "processed")).load(
        [(request, ingestion)],
        research_configuration(
            (request.period,),
            access_mode=ResearchAccessMode.FINAL_EVALUATION,
            authorized=True,
        ),
    )
    first = dataset.observations[0]

    assert first.available_at == first.open_time + timedelta(hours=1)
    assert first.available_at - first.close_time == timedelta(microseconds=1)
    assert first.close_time.microsecond == 999_999
    assert dataset.access_mode is ResearchAccessMode.FINAL_EVALUATION
    assert dataset.final_evaluation_authorized


def test_development_mode_rejects_holdout_before_storage(tmp_path, monkeypatch):
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
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,)),
        )


def test_final_evaluation_requires_explicit_authorization(tmp_path, monkeypatch):
    request = make_request(2024, 1)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("storage was called without authorization")
        ),
    )

    with pytest.raises(HoldoutAccessError, match="explicit authorization"):
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration(
                (request.period,),
                access_mode=ResearchAccessMode.FINAL_EVALUATION,
            ),
        )


def test_strict_missing_month_is_rejected_before_storage(tmp_path, monkeypatch):
    request = make_request(2024, 1)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("storage was called")
        ),
    )

    with pytest.raises(CoverageError, match="exactly once"):
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period, YearMonth(2024, 2))),
        )


def test_explicit_partial_coverage_is_not_described_as_complete(tmp_path):
    request = make_request(2024, 2)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    dataset = ResearchDataLoader(DuckDBParquetStore(tmp_path / "processed")).load(
        [(request, ingestion)],
        research_configuration(
            (request.period, YearMonth(2024, 3)),
            coverage_policy=CoveragePolicy.EXPLICIT_PARTIAL,
        ),
    )

    assert dataset.covered_periods == (request.period,)
    assert dataset.expected_periods == (request.period, YearMonth(2024, 3))
    assert not dataset.is_complete
    assert not dataset.covers_approved_history


def test_development_rejects_mixed_and_indirect_holdout_requests(
    tmp_path,
    monkeypatch,
):
    early = make_request(2024, 12)
    holdout = make_request(2025, 1)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("holdout storage was materialized")
        ),
    )
    loader = ResearchDataLoader(store)
    cases = (
        (
            [(early, _dummy_result(early)), (holdout, _dummy_result(holdout))],
            (early.period, holdout.period),
        ),
        ([(early, _dummy_result(early))], (early.period, holdout.period)),
        ([(holdout, _dummy_result(holdout))], (early.period,)),
    )

    for sources, periods in cases:
        with pytest.raises(HoldoutAccessError, match="development"):
            loader.load(sources, research_configuration(periods))


def test_development_rejects_authorization_flag_before_storage(tmp_path, monkeypatch):
    request = make_request(2024, 6)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("storage was called")
        ),
    )

    with pytest.raises(HoldoutAccessError, match="authorize"):
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,), authorized=True),
        )


def test_december_2024_reaches_storage_in_development(tmp_path, monkeypatch):
    request = make_request(2024, 12)
    store = DuckDBParquetStore(tmp_path / "processed")

    def reached_storage(*args, **kwargs):
        raise RuntimeError("reached storage")

    monkeypatch.setattr(store, "process_month", reached_storage)

    with pytest.raises(RuntimeError, match="reached storage"):
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,)),
        )


def test_malformed_month_requests_fail_before_storage(tmp_path, monkeypatch):
    january = make_request(2024, 1)
    february = make_request(2024, 2)
    april = make_request(2024, 4)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("storage was called")
        ),
    )
    loader = ResearchDataLoader(store)
    rejected = (
        (
            [(january, _dummy_result(january)), (january, _dummy_result(january))],
            (january.period,),
            "duplicate",
        ),
        (
            [(february, _dummy_result(february)), (january, _dummy_result(january))],
            (january.period, february.period),
            "chronological",
        ),
        (
            [(january, _dummy_result(january)), (february, _dummy_result(february))],
            (january.period,),
            "outside the expected",
        ),
        (
            [(february, _dummy_result(february)), (april, _dummy_result(april))],
            (february.period, april.period),
            "contiguous",
        ),
    )

    for sources, periods, message in rejected:
        with pytest.raises(CoverageError, match=message):
            loader.load(sources, research_configuration(periods))


def test_partial_noncontiguous_months_record_a_gap_without_bridging(tmp_path):
    from datetime import UTC, datetime

    from quantforge.research import MissingReason, ReturnKind, calculate_returns

    february = make_request(2024, 2)
    april = make_request(2024, 4)
    loader = ResearchDataLoader(DuckDBParquetStore(tmp_path / "processed"))
    dataset = loader.load(
        [
            (february, write_synthetic_source(tmp_path / "raw", february)),
            (april, write_synthetic_source(tmp_path / "raw", april)),
        ],
        research_configuration(
            (february.period, april.period),
            coverage_policy=CoveragePolicy.EXPLICIT_PARTIAL,
        ),
    )
    april_return = calculate_returns(dataset.observations, ReturnKind.SIMPLE)
    boundary = next(
        item
        for item in april_return
        if item.open_time == datetime(2024, 4, 1, tzinfo=UTC)
    )

    assert dataset.covered_periods == (february.period, april.period)
    assert dataset.gaps
    assert not dataset.is_complete
    assert not dataset.covers_approved_history
    assert boundary.missing_reason is MissingReason.GAP
    assert boundary.value is None


def test_stray_parquet_path_cannot_bypass_source_verification(tmp_path):
    import inspect

    request = make_request(2024, 6)
    store = DuckDBParquetStore(tmp_path / "processed")
    stray = store.partition_path(request)
    stray.mkdir(parents=True)
    (stray / f"{request.symbol}-{request.interval}-2024-06.parquet").write_bytes(
        b"not a verified partition"
    )

    assert tuple(inspect.signature(ResearchDataLoader.load).parameters) == (
        "self",
        "sources",
        "configuration",
    )
    with pytest.raises(SourceVerificationError, match="filename"):
        ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,)),
        )


def test_tampered_source_archive_is_rejected(tmp_path):
    request = make_request(2024, 3)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    ingestion.archive_path.write_bytes(
        ingestion.archive_path.read_bytes() + b"tampered"
    )

    with pytest.raises(SourceVerificationError):
        ResearchDataLoader(DuckDBParquetStore(tmp_path / "processed")).load(
            [(request, ingestion)],
            research_configuration((request.period,)),
        )
