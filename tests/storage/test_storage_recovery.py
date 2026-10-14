"""Failure, recovery, conflict, and validation-boundary storage tests."""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from dataclasses import replace
from decimal import Decimal

import pytest

import quantforge.storage.duckdb_parquet as storage_module
from quantforge.storage import (
    DataQualityError,
    DuckDBParquetStore,
    SourceVerificationError,
    StorageConflictError,
    StoragePublicationError,
    StorageValidationError,
)
from tests.support.synthetic_klines import (
    make_klines,
    make_request,
    write_synthetic_source,
)


def _temporary_directories(final_path):
    if not final_path.parent.exists():
        return []
    return list(
        final_path.parent.glob(f".month={final_path.name.removeprefix('month=')}.*.tmp")
    )


def test_storage_revalidates_source_and_rejects_invalid_observations(tmp_path):
    request = make_request(2024, 1)
    rows = list(make_klines(request))
    rows[0] = replace(rows[0], low=Decimal(40000))
    ingestion = write_synthetic_source(tmp_path / "raw", request, tuple(rows))
    store = DuckDBParquetStore(tmp_path / "processed")

    with pytest.raises(DataQualityError) as captured:
        store.process_month(request, ingestion)

    assert not captured.value.report.is_valid
    assert not store.partition_path(request).exists()


def test_corrupted_archive_and_forged_caller_provenance_are_rejected(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")

    ingestion.archive_path.write_bytes(
        ingestion.archive_path.read_bytes() + b"tampered"
    )
    with pytest.raises(SourceVerificationError, match="SHA-256"):
        store.process_month(request, ingestion)

    ingestion = write_synthetic_source(tmp_path / "raw", request)
    forged = replace(
        ingestion,
        provenance=replace(
            ingestion.provenance,
            archive_url="https://example.invalid/forged.zip",
        ),
    )
    with pytest.raises(SourceVerificationError, match="on-disk"):
        store.process_month(request, forged)


def test_source_mutation_during_parsing_is_rejected(
    tmp_path,
    monkeypatch,
):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    original_iter_klines = storage_module.iter_klines

    def mutating_iter_klines(path, source_request):
        yield from original_iter_klines(path, source_request)
        path.write_bytes(path.read_bytes() + b"concurrent mutation")

    monkeypatch.setattr(storage_module, "iter_klines", mutating_iter_klines)

    with pytest.raises(SourceVerificationError, match="changed during processing"):
        DuckDBParquetStore(tmp_path / "processed").process_month(
            request,
            ingestion,
        )


def test_source_mutation_before_publication_is_rejected(
    tmp_path,
    monkeypatch,
):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    original_build = store._build_temporary_partition

    def build_then_mutate(*args, **kwargs):
        result = original_build(*args, **kwargs)
        ingestion.provenance_path.write_bytes(
            ingestion.provenance_path.read_bytes() + b" "
        )
        return result

    monkeypatch.setattr(store, "_build_temporary_partition", build_then_mutate)

    with pytest.raises(SourceVerificationError, match="changed during processing"):
        store.process_month(request, ingestion)

    assert not store.partition_path(request).exists()
    assert not _temporary_directories(store.partition_path(request))


@pytest.mark.parametrize("failure_mode", ["missing", "invalid"])
def test_missing_or_invalid_source_provenance_is_rejected(tmp_path, failure_mode):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    if failure_mode == "missing":
        ingestion.provenance_path.unlink()
    else:
        ingestion.provenance_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(SourceVerificationError):
        DuckDBParquetStore(tmp_path / "processed").process_month(
            request,
            ingestion,
        )


def test_publisher_failure_cleans_temporary_output(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)

    def fail_publish(source, target):
        raise OSError("synthetic publication failure")

    store = DuckDBParquetStore(
        tmp_path / "processed",
        publisher=fail_publish,
    )
    final_path = store.partition_path(request)

    with pytest.raises(StoragePublicationError, match="synthetic"):
        store.process_month(request, ingestion)

    assert not final_path.exists()
    assert not _temporary_directories(final_path)

    recovered = DuckDBParquetStore(tmp_path / "processed").process_month(
        request,
        ingestion,
    )
    assert recovered.parquet_path.is_file()


def test_manifest_write_failure_cleans_temporary_output(tmp_path, monkeypatch):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    original_write = storage_module.write_json_file

    def fail_manifest(path, content):
        if path.name == "manifest.json":
            raise StorageValidationError("synthetic manifest failure")
        original_write(path, content)

    monkeypatch.setattr(storage_module, "write_json_file", fail_manifest)
    store = DuckDBParquetStore(tmp_path / "processed")
    final_path = store.partition_path(request)

    with pytest.raises(StorageValidationError, match="synthetic"):
        store.process_month(request, ingestion)

    assert not final_path.exists()
    assert not _temporary_directories(final_path)


def test_parquet_write_failure_cleans_temporary_output(tmp_path, monkeypatch):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    final_path = store.partition_path(request)

    def fail_write(*args, **kwargs):
        raise StorageValidationError("synthetic Parquet write failure")

    monkeypatch.setattr(store, "_write_parquet", fail_write)

    with pytest.raises(StorageValidationError, match="Parquet write"):
        store.process_month(request, ingestion)

    assert not final_path.exists()
    assert not _temporary_directories(final_path)


def test_readback_failure_cleans_temporary_output(tmp_path, monkeypatch):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    final_path = store.partition_path(request)

    def fail_readback(*args, **kwargs):
        raise StorageValidationError("synthetic readback failure")

    monkeypatch.setattr(store, "_verify_parquet", fail_readback)

    with pytest.raises(StorageValidationError, match="readback"):
        store.process_month(request, ingestion)

    assert not final_path.exists()
    assert not _temporary_directories(final_path)


@pytest.mark.parametrize(
    "corrupt_name",
    ["manifest.json", "validation.json", "parquet"],
)
def test_corrupted_existing_partition_is_never_reused(
    tmp_path,
    corrupt_name,
):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    first = store.process_month(request, ingestion)
    path = (
        first.parquet_path
        if corrupt_name == "parquet"
        else first.partition_path / corrupt_name
    )
    path.write_bytes(b"corrupted")

    with pytest.raises(StorageValidationError):
        store.process_month(request, ingestion)


def test_incomplete_existing_partition_is_rejected_without_overwrite(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    final_path = store.partition_path(request)
    final_path.mkdir(parents=True)
    sentinel = final_path / "do-not-overwrite"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(StorageValidationError, match="incomplete"):
        store.process_month(request, ingestion)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_unexpected_existing_partition_entry_is_rejected(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    result = store.process_month(request, ingestion)
    unexpected = result.partition_path / "stale-output"
    unexpected.write_text("unexpected", encoding="utf-8")

    with pytest.raises(StorageValidationError, match="unexpected entries"):
        store.process_month(request, ingestion)

    assert unexpected.read_text(encoding="utf-8") == "unexpected"


def test_incompatible_existing_schema_raises_conflict(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    result = store.process_month(request, ingestion)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    payload["storage_schema_version"] = "unsupported"
    result.manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(StorageConflictError, match="processing versions"):
        store.process_month(request, ingestion)


def test_changed_source_archive_raises_explicit_conflict(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)
    store = DuckDBParquetStore(tmp_path / "processed")
    store.process_month(request, ingestion)

    corrected_rows = list(make_klines(request))
    corrected_rows[0] = replace(corrected_rows[0], close=Decimal("30000.5"))
    corrected = write_synthetic_source(
        tmp_path / "raw",
        request,
        tuple(corrected_rows),
    )

    with pytest.raises(StorageConflictError, match="different source archive"):
        store.process_month(request, corrected)


def test_concurrent_complete_publication_is_verified_and_reused(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)

    def concurrent_publish(source, target):
        assert source.parent == target.parent
        competitor = target.with_name(f".{target.name}.competitor")
        shutil.copytree(source, competitor)
        os.rename(competitor, target)
        os.rename(source, target)

    store = DuckDBParquetStore(
        tmp_path / "processed",
        publisher=concurrent_publish,
    )

    result = store.process_month(request, ingestion)

    assert result.reused_existing_partition
    assert result.parquet_path.is_file()
    assert not _temporary_directories(result.partition_path)


def test_publisher_cannot_report_success_without_publishing(tmp_path):
    request = make_request(2024, 1)
    ingestion = write_synthetic_source(tmp_path / "raw", request)

    def no_op_publisher(source, target):
        return None

    store = DuckDBParquetStore(
        tmp_path / "processed",
        publisher=no_op_publisher,
    )
    final_path = store.partition_path(request)

    with pytest.raises(StoragePublicationError, match="without creating"):
        store.process_month(request, ingestion)

    assert not final_path.exists()
    assert not _temporary_directories(final_path)


def test_excess_decimal_scale_cannot_bypass_storage_validation(tmp_path):
    request = make_request(2024, 1)
    rows = list(make_klines(request))
    rows[0] = replace(rows[0], volume=Decimal("1.0000000000000000001"))
    ingestion = write_synthetic_source(tmp_path / "raw", request, tuple(rows))
    store = DuckDBParquetStore(tmp_path / "processed")

    with pytest.raises(DataQualityError) as captured:
        store.process_month(request, ingestion)

    assert not captured.value.report.is_valid
    assert not store.partition_path(request).exists()


def test_storage_path_does_not_use_network(tmp_path, monkeypatch):
    request = make_request(2024, 2)
    ingestion = write_synthetic_source(tmp_path / "raw", request)

    def reject_network(*args, **kwargs):
        raise AssertionError("network access is forbidden in M3 tests")

    monkeypatch.setattr(urllib.request, "urlopen", reject_network)

    result = DuckDBParquetStore(tmp_path / "processed").process_month(
        request,
        ingestion,
    )

    assert result.parquet_path.is_file()
