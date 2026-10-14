"""Dataset-boundary tests for momentum signals."""

from datetime import UTC, datetime, timedelta
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
from quantforge.research import (
    CoveragePolicy,
    HoldoutAccessError,
    PartitionLineage,
    ResearchAccessMode,
    ResearchDataset,
)
from quantforge.storage import DuckDBParquetStore
from quantforge.strategies import MomentumConfig, MomentumStrategy, SignalAction
from tests.research.support import hourly_prices, research_configuration
from tests.support.synthetic_klines import FIXED_RETRIEVAL_TIME, make_request


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


def _dataset(
    observations,
    period: YearMonth,
    *,
    access_mode: ResearchAccessMode = ResearchAccessMode.DEVELOPMENT,
    authorized: bool = False,
) -> ResearchDataset:
    return ResearchDataset(
        observations=tuple(observations),
        expected_periods=(period,),
        covered_periods=(period,),
        gaps=(),
        lineage=(
            PartitionLineage(
                year=period.year,
                month=period.month,
                source_archive_sha256="a" * 64,
                source_provenance_sha256="b" * 64,
                parquet_sha256="c" * 64,
                validation_sha256="d" * 64,
                observation_count=len(observations),
                warning_count=0,
            ),
        ),
        warnings=(),
        access_mode=access_mode,
        coverage_policy=CoveragePolicy.STRICT,
        final_evaluation_authorized=authorized,
        is_complete=True,
    )


def test_development_loader_rejects_holdout_before_storage(tmp_path, monkeypatch):
    import urllib.request

    request = make_request(2025, 1)
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("holdout storage was materialized")
        ),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network access")),
    )

    def load_and_signal():
        from quantforge.research import ResearchDataLoader

        dataset = ResearchDataLoader(store).load(
            [(request, _dummy_result(request))],
            research_configuration((request.period,)),
        )
        return MomentumStrategy().generate_from_dataset(dataset)

    with pytest.raises(HoldoutAccessError, match="development"):
        load_and_signal()


def test_development_dataset_rejects_holdout_but_pure_2025_timestamps_are_allowed():
    period = YearMonth(2025, 1)
    observations = hourly_prices(datetime(2025, 6, 1, tzinfo=UTC), (100, 110))
    synthetic = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate(
        observations
    )

    assert synthetic[-1].action is SignalAction.LONG
    assert synthetic[-1].available_at - synthetic[-1].close_time == timedelta(
        microseconds=1
    )
    with pytest.raises(HoldoutAccessError, match="development"):
        MomentumStrategy().generate_from_dataset(_dataset(observations, period))


def test_development_dataset_can_signal_2024_without_performance_fields():
    period = YearMonth(2024, 6)
    observations = hourly_prices(datetime(2024, 6, 1, tzinfo=UTC), (100, 90))
    signal = MomentumStrategy(MomentumConfig(lookback_hours=1)).generate_from_dataset(
        _dataset(observations, period)
    )[-1]

    assert signal.action is SignalAction.FLAT
    assert not hasattr(signal, "pnl")
    assert not hasattr(signal, "quantity")


def test_final_evaluation_mode_is_rejected_without_loading_storage(
    tmp_path, monkeypatch
):
    period = YearMonth(2024, 6)
    observations = hourly_prices(datetime(2024, 6, 1, tzinfo=UTC), (100, 110))
    store = DuckDBParquetStore(tmp_path / "processed")
    monkeypatch.setattr(
        store,
        "process_month",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("final evaluation storage was materialized")
        ),
    )

    with pytest.raises(HoldoutAccessError, match="final holdout"):
        MomentumStrategy().generate_from_dataset(
            _dataset(
                observations,
                period,
                access_mode=ResearchAccessMode.FINAL_EVALUATION,
                authorized=True,
            )
        )
