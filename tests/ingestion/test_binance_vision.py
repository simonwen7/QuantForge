from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import Self
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

import quantforge.ingestion.binance_vision as binance_vision_module
from quantforge.ingestion.binance_vision import (
    BinanceVisionIngestor,
    archive_url,
    checksum_url,
    enumerate_monthly_requests,
    full_history_requests,
    parse_checksum,
)
from quantforge.ingestion.models import (
    ChecksumError,
    CorrectionConflictError,
    DownloadError,
    MonthlyKlineRequest,
    ProvenanceError,
    PublicationError,
    RequestValidationError,
    RetryPolicy,
    YearMonth,
)
from quantforge.ingestion.provenance import read_provenance


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        fail_after_bytes: int | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {"content-length": str(len(body))})
        self._body = body
        self._position = 0
        self._fail_after_bytes = fail_after_bytes

    def read(self, size: int = -1) -> bytes:
        if (
            self._fail_after_bytes is not None
            and self._position >= self._fail_after_bytes
        ):
            raise TimeoutError("synthetic interrupted transfer")
        if self._position >= len(self._body):
            return b""
        requested_end = len(self._body) if size < 0 else self._position + size
        if self._fail_after_bytes is not None:
            requested_end = min(requested_end, self._fail_after_bytes)
        end = min(requested_end, len(self._body))
        chunk = self._body[self._position : end]
        self._position = end
        return chunk

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class ScriptedTransport:
    def __init__(
        self,
        scripts: list[tuple[str, FakeResponse | BaseException]],
    ) -> None:
        self.scripts = scripts
        self.calls: list[tuple[str, float, float]] = []

    def open(
        self,
        url: str,
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> FakeResponse:
        self.calls.append((url, connect_timeout, read_timeout))
        if not self.scripts:
            raise AssertionError(f"unexpected network call: {url}")
        expected_url, result = self.scripts.pop(0)
        assert url == expected_url
        if isinstance(result, BaseException):
            raise result
        return result


def _synthetic_archive(request: MonthlyKlineRequest) -> bytes:
    if request.period.year == 2025:
        open_time = "1735689600000000"
        close_time = "1735693199999999"
    else:
        open_time = "1704067200000"
        close_time = "1704070799999"
    row = f"{open_time},100.0,101.0,99.0,100.5,10.0,{close_time},1005.0,12,6.0,603.0,0"
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(request.csv_filename, f"{row}\n")
    return output.getvalue()


def _checksum_response(request: MonthlyKlineRequest, digest: str) -> FakeResponse:
    body = f"{digest}  {request.archive_filename}\n".encode()
    return FakeResponse(
        body,
        headers={
            "content-length": str(len(body)),
            "last-modified": "Thu, 01 Jan 2026 00:00:00 GMT",
        },
    )


def _successful_transport(
    request: MonthlyKlineRequest,
) -> tuple[ScriptedTransport, bytes, str]:
    archive = _synthetic_archive(request)
    digest = hashlib.sha256(archive).hexdigest()
    transport = ScriptedTransport(
        [
            (checksum_url(request), _checksum_response(request, digest)),
            (
                archive_url(request),
                FakeResponse(
                    archive,
                    headers={
                        "content-length": str(len(archive)),
                        "etag": '"synthetic-etag"',
                    },
                ),
            ),
        ]
    )
    return transport, archive, digest


def test_monthly_urls_and_full_history_enumeration() -> None:
    requests = full_history_requests()

    assert len(requests) == 60
    assert requests[0].period == YearMonth(2021, 1)
    assert requests[-1].period == YearMonth(2025, 12)
    assert archive_url(requests[0]) == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "BTCUSDT/1h/BTCUSDT-1h-2021-01.zip"
    )
    assert checksum_url(requests[0]).endswith(".zip.CHECKSUM")
    assert enumerate_monthly_requests(YearMonth(2024, 12), YearMonth(2025, 1)) == (
        MonthlyKlineRequest(YearMonth(2024, 12)),
        MonthlyKlineRequest(YearMonth(2025, 1)),
    )


def test_request_scope_is_strictly_validated() -> None:
    with pytest.raises(RequestValidationError, match="only BTCUSDT Spot 1h"):
        MonthlyKlineRequest(YearMonth(2024, 1), symbol="ETHUSDT")


def test_checksum_parser_is_strict() -> None:
    filename = "BTCUSDT-1h-2024-01.zip"
    digest = "a" * 64

    assert parse_checksum(f"{digest}  {filename}\n", filename) == digest
    assert parse_checksum(f"{digest} *{filename}\n", filename) == digest
    with pytest.raises(ChecksumError, match="exactly one"):
        parse_checksum(f"{digest}  {filename}\nextra\n", filename)
    with pytest.raises(ChecksumError, match="expected"):
        parse_checksum(f"{digest}  wrong.zip\n", filename)


def test_ingestion_verifies_checksum_and_writes_provenance(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, archive, digest = _successful_transport(request)
    retrieved_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        clock=lambda: retrieved_at,
    )

    result = ingestor.ingest(request)

    assert result.archive_path.read_bytes() == archive
    assert result.provenance.actual_sha256 == digest
    assert result.provenance.retrieved_at_utc == retrieved_at
    assert result.provenance.archive_http_metadata == {
        "content-length": str(len(archive)),
        "etag": '"synthetic-etag"',
    }
    assert read_provenance(result.provenance_path) == result.provenance
    assert not result.reused_existing_archive
    assert all(
        connect_timeout == 10.0 and read_timeout == 30.0
        for _, connect_timeout, read_timeout in transport.calls
    )


def test_incorrect_checksum_rejects_archive_and_cleans_temp(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = _synthetic_archive(request)
    transport = ScriptedTransport(
        [
            (checksum_url(request), _checksum_response(request, "0" * 64)),
            (archive_url(request), FakeResponse(archive)),
        ]
    )
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)

    with pytest.raises(ChecksumError, match="does not match"):
        ingestor.ingest(request)

    assert not ingestor.archive_path(request).exists()
    assert not list(tmp_path.rglob("*.part"))


def test_non_transient_http_failure_is_not_retried(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport = ScriptedTransport(
        [(checksum_url(request), FakeResponse(b"missing", status=404))]
    )
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)

    with pytest.raises(DownloadError, match="HTTP 404"):
        ingestor.ingest(request)

    assert len(transport.calls) == 1


def test_partial_download_is_removed(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = _synthetic_archive(request)
    digest = hashlib.sha256(archive).hexdigest()
    partial_response = FakeResponse(
        archive,
        headers={"content-length": str(len(archive))},
        fail_after_bytes=10,
    )
    transport = ScriptedTransport(
        [
            (checksum_url(request), _checksum_response(request, digest)),
            (archive_url(request), partial_response),
        ]
    )
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        retries=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(DownloadError, match="after 1 attempts"):
        ingestor.ingest(request)

    assert not ingestor.archive_path(request).exists()
    assert not list(tmp_path.rglob("*.part"))


def test_transient_failure_retries_with_bounded_backoff(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = _synthetic_archive(request)
    digest = hashlib.sha256(archive).hexdigest()
    sleeps: list[float] = []
    transport = ScriptedTransport(
        [
            (checksum_url(request), TimeoutError("synthetic timeout")),
            (checksum_url(request), _checksum_response(request, digest)),
            (archive_url(request), FakeResponse(archive)),
        ]
    )
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        retries=RetryPolicy(max_attempts=2, backoff_seconds=0.1),
        sleeper=sleeps.append,
    )

    result = ingestor.ingest(request)

    assert result.archive_path.exists()
    assert sleeps == [0.1]
    assert len(transport.calls) == 3


def test_interrupted_archive_retry_restarts_from_clean_temp_file(
    tmp_path: Path,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = _synthetic_archive(request)
    digest = hashlib.sha256(archive).hexdigest()
    sleeps: list[float] = []
    transport = ScriptedTransport(
        [
            (checksum_url(request), _checksum_response(request, digest)),
            (
                archive_url(request),
                FakeResponse(archive, fail_after_bytes=10),
            ),
            (archive_url(request), FakeResponse(archive)),
        ]
    )
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        retries=RetryPolicy(max_attempts=2, backoff_seconds=0.1),
        sleeper=sleeps.append,
    )

    result = ingestor.ingest(request)

    assert result.archive_path.read_bytes() == archive
    assert result.provenance.actual_sha256 == digest
    assert sleeps == [0.1]
    assert not list(tmp_path.rglob("*.part"))


def test_repeated_ingestion_reuses_verified_archive(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, _, digest = _successful_transport(request)
    transport.scripts.append(
        (checksum_url(request), _checksum_response(request, digest))
    )
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = ingestor.ingest(request)
    second = ingestor.ingest(request)

    assert not first.reused_existing_archive
    assert second.reused_existing_archive
    assert second.provenance == first.provenance
    assert len(transport.calls) == 3


def test_orphan_archive_is_not_accepted_with_fabricated_provenance(
    tmp_path: Path,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    archive = _synthetic_archive(request)
    digest = hashlib.sha256(archive).hexdigest()
    transport = ScriptedTransport(
        [(checksum_url(request), _checksum_response(request, digest))]
    )
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)
    archive_path = ingestor.archive_path(request)
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(archive)

    with pytest.raises(ProvenanceError, match="cannot be reconstructed"):
        ingestor.ingest(request)

    assert not ingestor.provenance_path(request).exists()


def test_remote_archive_correction_raises_conflict(tmp_path: Path) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, archive, _ = _successful_transport(request)
    ingestor = BinanceVisionIngestor(
        tmp_path,
        transport=transport,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    first = ingestor.ingest(request)
    provenance_before = first.provenance_path.read_bytes()
    transport.scripts.append(
        (checksum_url(request), _checksum_response(request, "0" * 64))
    )

    with pytest.raises(CorrectionConflictError, match="replacement is prohibited"):
        ingestor.ingest(request)

    assert first.archive_path.read_bytes() == archive
    assert first.provenance_path.read_bytes() == provenance_before


def test_invalid_existing_provenance_rejects_reuse_and_preserves_archive(
    tmp_path: Path,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, archive, digest = _successful_transport(request)
    transport.scripts.append(
        (checksum_url(request), _checksum_response(request, digest))
    )
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)
    first = ingestor.ingest(request)
    first.provenance_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ProvenanceError, match="cannot read provenance"):
        ingestor.ingest(request)

    assert first.archive_path.read_bytes() == archive


def test_local_archive_corruption_is_not_treated_as_remote_correction(
    tmp_path: Path,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, _, digest = _successful_transport(request)
    transport.scripts.append(
        (checksum_url(request), _checksum_response(request, digest))
    )
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)
    first = ingestor.ingest(request)
    first.archive_path.write_bytes(b"locally corrupted")

    with pytest.raises(ChecksumError, match="no longer matches its provenance"):
        ingestor.ingest(request)


def test_archive_publication_failure_leaves_no_accepted_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, _, _ = _successful_transport(request)
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)

    def fail_link(source: Path, destination: Path) -> None:
        raise OSError("synthetic link failure")

    with monkeypatch.context() as patch:
        patch.setattr(binance_vision_module.os, "link", fail_link)
        with pytest.raises(PublicationError, match="cannot publish validated archive"):
            ingestor.ingest(request)

    assert not ingestor.archive_path(request).exists()
    assert not ingestor.provenance_path(request).exists()
    assert not list(tmp_path.rglob("*.part"))

    retry_transport, _, _ = _successful_transport(request)
    recovered = BinanceVisionIngestor(
        tmp_path,
        transport=retry_transport,
    ).ingest(request)
    assert recovered.archive_path.exists()
    assert recovered.provenance_path.exists()


def test_concurrent_archive_publication_is_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, archive, _ = _successful_transport(request)
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)

    def publish_concurrently(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes())
        raise FileExistsError("synthetic concurrent publication")

    with monkeypatch.context() as patch:
        patch.setattr(
            binance_vision_module.os,
            "link",
            publish_concurrently,
        )
        with pytest.raises(PublicationError, match="appeared concurrently"):
            ingestor.ingest(request)

    assert ingestor.archive_path(request).read_bytes() == archive
    assert not ingestor.provenance_path(request).exists()
    assert not list(tmp_path.rglob("*.part"))


def test_provenance_failure_rolls_back_new_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = MonthlyKlineRequest(YearMonth(2024, 1))
    transport, _, _ = _successful_transport(request)
    ingestor = BinanceVisionIngestor(tmp_path, transport=transport)

    def fail_provenance(path: Path, record: object) -> None:
        raise ProvenanceError("synthetic provenance failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            binance_vision_module,
            "write_provenance_atomic",
            fail_provenance,
        )
        with pytest.raises(ProvenanceError, match="synthetic provenance failure"):
            ingestor.ingest(request)

    assert not ingestor.archive_path(request).exists()
    assert not ingestor.provenance_path(request).exists()
    assert not list(tmp_path.rglob("*.part"))

    retry_transport, _, _ = _successful_transport(request)
    recovered = BinanceVisionIngestor(
        tmp_path,
        transport=retry_transport,
    ).ingest(request)
    assert recovered.archive_path.exists()
    assert recovered.provenance_path.exists()
