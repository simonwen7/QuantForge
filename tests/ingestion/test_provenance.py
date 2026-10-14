from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import quantforge.ingestion.provenance as provenance_module
from quantforge.ingestion.models import ProvenanceError, ProvenanceRecord, TimestampUnit
from quantforge.ingestion.provenance import (
    read_provenance,
    serialize_provenance,
    write_provenance_atomic,
)


def _record() -> ProvenanceRecord:
    return ProvenanceRecord(
        source_name="Binance Vision",
        archive_url="https://example.test/archive.zip",
        checksum_url="https://example.test/archive.zip.CHECKSUM",
        symbol="BTCUSDT",
        market="spot",
        interval="1h",
        year=2025,
        month=1,
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        archive_size=1234,
        retrieved_at_utc=datetime(2026, 1, 2, 3, 4, 5, 6789, tzinfo=UTC),
        source_timestamp_unit=TimestampUnit.MICROSECONDS,
        schema_version="binance-spot-kline-12-v1",
        parser_version="1",
        dataset_terms_url="https://example.test/terms",
        archive_member_name="BTCUSDT-1h-2025-01.csv",
        archive_http_metadata={"etag": '"archive-etag"'},
        checksum_http_metadata={"last-modified": "Thu, 01 Jan 2026 00:00:00 GMT"},
    )


def test_serialization_is_deterministic_and_contains_observed_metadata() -> None:
    record = _record()

    first = serialize_provenance(record)
    second = serialize_provenance(record)

    assert first == second
    assert first.endswith("\n")
    assert '"retrieved_at_utc": "2026-01-02T03:04:05.006789Z"' in first
    assert '"etag": "\\"archive-etag\\""' in first


def test_unobserved_http_metadata_is_omitted() -> None:
    serialized = serialize_provenance(
        replace(
            _record(),
            archive_http_metadata={},
            checksum_http_metadata={},
        )
    )

    assert "archive_http_metadata" not in serialized
    assert "checksum_http_metadata" not in serialized


def test_atomic_write_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "archive.provenance.json"
    record = _record()

    write_provenance_atomic(path, record)
    first_bytes = path.read_bytes()
    write_provenance_atomic(path, record)

    assert path.read_bytes() == first_bytes
    assert read_provenance(path) == record
    assert not list(tmp_path.glob("*.part"))


def test_atomic_write_rejects_conflicting_existing_record(tmp_path: Path) -> None:
    path = tmp_path / "archive.provenance.json"
    write_provenance_atomic(path, _record())
    conflicting = replace(
        _record(),
        retrieved_at_utc=_record().retrieved_at_utc + timedelta(seconds=1),
    )

    with pytest.raises(ProvenanceError, match="conflicts"):
        write_provenance_atomic(path, conflicting)


def test_atomic_write_failure_leaves_no_sidecar_or_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "archive.provenance.json"

    def fail_link(source: Path, destination: Path) -> None:
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(provenance_module.os, "link", fail_link)

    with pytest.raises(ProvenanceError, match="cannot publish provenance"):
        write_provenance_atomic(path, _record())

    assert not path.exists()
    assert not list(tmp_path.glob("*.part"))


def test_provenance_directory_failure_is_typed(tmp_path: Path) -> None:
    blocked_directory = tmp_path / "blocked"
    blocked_directory.write_text("not a directory", encoding="utf-8")
    path = blocked_directory / "archive.provenance.json"

    with pytest.raises(ProvenanceError, match="cannot create provenance directory"):
        write_provenance_atomic(path, _record())
