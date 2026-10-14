"""Binance Vision adapter with offline-testable, atomic ingestion."""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import urlsplit

from quantforge.ingestion.klines import validate_kline_archive
from quantforge.ingestion.models import (
    DATASET_TERMS_URL,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SOURCE_NAME,
    ChecksumError,
    CorrectionConflictError,
    DownloadError,
    HttpTimeouts,
    IngestionResult,
    MonthlyKlineRequest,
    ProvenanceError,
    ProvenanceRecord,
    PublicationError,
    RetryPolicy,
    SafetyLimits,
    TransientDownloadError,
    YearMonth,
)
from quantforge.ingestion.provenance import (
    read_provenance,
    write_provenance_atomic,
)

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
_CHECKSUM_PATTERN = re.compile(r"\A([0-9a-fA-F]{64})[ \t]+(?:\*)?([^\r\n]+)\r?\n?\Z")
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_METADATA_HEADERS = ("content-length", "etag", "last-modified")
_CHECKSUM_MAX_BYTES = 4 * 1024

logger = logging.getLogger(__name__)


class DownloadResponse(Protocol):
    """Minimal streaming HTTP response required by the adapter."""

    status: int
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` response bytes."""

    def __enter__(self) -> Self:
        """Enter a response context."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Close the response context."""


class DownloadTransport(Protocol):
    """Injectable transport used to keep tests completely offline."""

    def open(
        self,
        url: str,
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> DownloadResponse:
        """Open a streaming GET response."""


class _HttpsResponse(AbstractContextManager["_HttpsResponse"]):
    def __init__(
        self,
        connection: http.client.HTTPSConnection,
        response: http.client.HTTPResponse,
    ) -> None:
        self._connection = connection
        self._response = response
        self.status = response.status
        self.headers = {key.lower(): value for key, value in response.getheaders()}

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._response.close()
        self._connection.close()


class HttpsTransport:
    """Standard-library HTTPS transport with separate connect/read timeouts."""

    def open(
        self,
        url: str,
        *,
        connect_timeout: float,
        read_timeout: float,
    ) -> DownloadResponse:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise DownloadError(f"only absolute HTTPS URLs are supported: {url!r}")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"

        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=connect_timeout,
        )
        try:
            connection.request("GET", target, headers={"User-Agent": "quantforge/0.1"})
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
        except Exception:
            connection.close()
            raise
        return _HttpsResponse(connection, response)


def archive_url(request: MonthlyKlineRequest) -> str:
    """Return the deterministic monthly archive URL."""
    return f"{BASE_URL}/{request.symbol}/{request.interval}/{request.archive_filename}"


def checksum_url(request: MonthlyKlineRequest) -> str:
    """Return the checksum URL corresponding to a monthly archive."""
    return f"{archive_url(request)}.CHECKSUM"


def enumerate_monthly_requests(
    start: YearMonth,
    end: YearMonth,
) -> tuple[MonthlyKlineRequest, ...]:
    """Enumerate an inclusive, ordered month range within 2021-2025."""
    if end < start:
        raise ValueError(f"end month {end} precedes start month {start}")
    requests: list[MonthlyKlineRequest] = []
    current = start
    while True:
        requests.append(MonthlyKlineRequest(period=current))
        if current == end:
            break
        current = current.next()
    return tuple(requests)


def full_history_requests() -> tuple[MonthlyKlineRequest, ...]:
    """Return all 60 approved monthly requests from 2021 through 2025."""
    return enumerate_monthly_requests(YearMonth(2021, 1), YearMonth(2025, 12))


def parse_checksum(text: str, expected_filename: str) -> str:
    """Parse one strict SHA-256 checksum line for the expected archive."""
    match = _CHECKSUM_PATTERN.fullmatch(text)
    if match is None:
        raise ChecksumError("checksum response must contain exactly one SHA-256 line")
    digest, filename = match.groups()
    if filename != expected_filename:
        raise ChecksumError(
            f"checksum names {filename!r}; expected {expected_filename!r}"
        )
    return digest.lower()


def sha256_file(path: Path) -> str:
    """Calculate a file SHA-256 using bounded memory."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(128 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ChecksumError(f"cannot hash archive {path}: {error}") from error
    return digest.hexdigest()


def _observed_http_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {key.lower(): value for key, value in headers.items()}
    return {key: normalized[key] for key in _METADATA_HEADERS if key in normalized}


def _validate_status(status: int, url: str) -> None:
    if status == 200:
        return
    if status in _TRANSIENT_STATUS_CODES:
        raise TransientDownloadError(f"transient HTTP {status} for {url}")
    raise DownloadError(f"unexpected HTTP {status} for {url}")


def _content_length(headers: Mapping[str, str], url: str) -> int | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    value = normalized.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise DownloadError(
            f"invalid Content-Length header for {url}: {value!r}"
        ) from error
    if parsed < 0:
        raise DownloadError(f"negative Content-Length header for {url}")
    return parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class BinanceVisionIngestor:
    """Ingest validated monthly archives into deterministic local paths."""

    root: Path
    transport: DownloadTransport = field(default_factory=HttpsTransport)
    timeouts: HttpTimeouts = field(default_factory=HttpTimeouts)
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    safety_limits: SafetyLimits = field(default_factory=SafetyLimits)
    clock: Callable[[], datetime] = _utc_now
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def archive_path(self, request: MonthlyKlineRequest) -> Path:
        """Return the canonical local archive path."""
        return (
            self.root
            / "binance-vision"
            / request.market
            / "klines"
            / request.symbol
            / request.interval
            / f"{request.period.year:04d}"
            / request.archive_filename
        )

    def provenance_path(self, request: MonthlyKlineRequest) -> Path:
        """Return the provenance sidecar path for a request."""
        return self.archive_path(request).with_suffix(".provenance.json")

    def ingest_range(
        self,
        start: YearMonth,
        end: YearMonth,
    ) -> Iterator[IngestionResult]:
        """Ingest an inclusive month range sequentially."""
        for request in enumerate_monthly_requests(start, end):
            yield self.ingest(request)

    def ingest(self, request: MonthlyKlineRequest) -> IngestionResult:
        """Retrieve, validate, and atomically publish one monthly archive."""
        remote_checksum, checksum_metadata = self._fetch_checksum(request)
        final_archive = self.archive_path(request)
        final_provenance = self.provenance_path(request)

        if final_archive.exists():
            return self._reuse_existing(
                request,
                final_archive,
                final_provenance,
                remote_checksum,
            )

        temporary_archive, actual_checksum, archive_size, archive_metadata = (
            self._download_archive(request, remote_checksum, final_archive.parent)
        )
        published = False
        try:
            validation = validate_kline_archive(
                temporary_archive,
                request,
                self.safety_limits,
            )
            record = self._make_provenance(
                request=request,
                expected_checksum=remote_checksum,
                actual_checksum=actual_checksum,
                archive_size=archive_size,
                archive_member_name=validation.member_name,
                archive_metadata=archive_metadata,
                checksum_metadata=checksum_metadata,
            )
            final_archive.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(temporary_archive, final_archive)
            except FileExistsError as error:
                raise PublicationError(
                    f"archive appeared concurrently at {final_archive}; "
                    "retry ingestion to validate it"
                ) from error
            except OSError as error:
                raise PublicationError(
                    f"cannot publish validated archive at {final_archive}: {error}"
                ) from error
            published = True

            try:
                write_provenance_atomic(final_provenance, record)
            except ProvenanceError:
                try:
                    final_archive.unlink()
                    published = False
                except OSError as rollback_error:
                    raise PublicationError(
                        "provenance publication failed and the newly published "
                        f"archive could not be rolled back: {rollback_error}"
                    ) from rollback_error
                raise

            return IngestionResult(
                archive_path=final_archive,
                provenance_path=final_provenance,
                provenance=record,
                reused_existing_archive=False,
            )
        finally:
            temporary_archive.unlink(missing_ok=True)
            if published and not final_provenance.exists():
                logger.error(
                    "archive exists without provenance after an interrupted "
                    "publication: %s",
                    final_archive,
                )

    def _reuse_existing(
        self,
        request: MonthlyKlineRequest,
        archive_path_value: Path,
        provenance_path_value: Path,
        remote_checksum: str,
    ) -> IngestionResult:
        existing_record = (
            read_provenance(provenance_path_value)
            if provenance_path_value.exists()
            else None
        )
        actual_checksum = sha256_file(archive_path_value)
        if (
            existing_record is not None
            and actual_checksum != existing_record.actual_sha256
        ):
            raise ChecksumError(
                f"local archive no longer matches its provenance: {archive_path_value}"
            )
        if actual_checksum != remote_checksum:
            raise CorrectionConflictError(
                "remote archive checksum differs from the accepted local archive; "
                "automatic replacement is prohibited"
            )

        validation = validate_kline_archive(
            archive_path_value,
            request,
            self.safety_limits,
        )
        archive_size = archive_path_value.stat().st_size
        if existing_record is None:
            raise ProvenanceError(
                "a valid archive exists without provenance; its retrieval time "
                "and HTTP metadata cannot be reconstructed safely"
            )
        self._validate_existing_provenance(
            existing_record,
            request,
            remote_checksum,
            archive_size,
            validation.member_name,
        )

        return IngestionResult(
            archive_path=archive_path_value,
            provenance_path=provenance_path_value,
            provenance=existing_record,
            reused_existing_archive=True,
        )

    def _fetch_checksum(
        self,
        request: MonthlyKlineRequest,
    ) -> tuple[str, Mapping[str, str]]:
        url = checksum_url(request)

        def operation() -> tuple[str, Mapping[str, str]]:
            with self.transport.open(
                url,
                connect_timeout=self.timeouts.connect_seconds,
                read_timeout=self.timeouts.read_seconds,
            ) as response:
                _validate_status(response.status, url)
                expected_length = _content_length(response.headers, url)
                if (
                    expected_length is not None
                    and expected_length > _CHECKSUM_MAX_BYTES
                ):
                    raise ChecksumError("checksum response is unexpectedly large")
                body = bytearray()
                while True:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > _CHECKSUM_MAX_BYTES:
                        raise ChecksumError("checksum response is unexpectedly large")
                if expected_length is not None and len(body) != expected_length:
                    raise TransientDownloadError(
                        f"incomplete checksum response from {url}"
                    )
                try:
                    text = bytes(body).decode("utf-8", errors="strict")
                except UnicodeDecodeError as error:
                    raise ChecksumError(
                        "checksum response is not valid UTF-8"
                    ) from error
                return (
                    parse_checksum(text, request.archive_filename),
                    _observed_http_metadata(response.headers),
                )

        return self._retry(url, operation)

    def _download_archive(
        self,
        request: MonthlyKlineRequest,
        expected_checksum: str,
        destination_directory: Path,
    ) -> tuple[Path, str, int, Mapping[str, str]]:
        url = archive_url(request)

        def operation() -> tuple[Path, str, int, Mapping[str, str]]:
            return self._download_archive_once(
                url,
                expected_checksum,
                request.archive_filename,
                destination_directory,
            )

        return self._retry(url, operation)

    def _download_archive_once(
        self,
        url: str,
        expected_checksum: str,
        filename: str,
        destination_directory: Path,
    ) -> tuple[Path, str, int, Mapping[str, str]]:
        try:
            destination_directory.mkdir(parents=True, exist_ok=True)
            temporary = tempfile.NamedTemporaryFile(  # noqa: SIM115
                mode="w+b",
                prefix=f".{filename}.",
                suffix=".part",
                dir=destination_directory,
                delete=False,
            )
        except OSError as error:
            raise PublicationError(
                f"cannot create temporary archive in {destination_directory}: {error}"
            ) from error

        temporary_path = Path(temporary.name)
        completed = False
        try:
            with (
                temporary,
                self.transport.open(
                    url,
                    connect_timeout=self.timeouts.connect_seconds,
                    read_timeout=self.timeouts.read_seconds,
                ) as response,
            ):
                _validate_status(response.status, url)
                expected_length = _content_length(response.headers, url)
                if (
                    expected_length is not None
                    and expected_length > self.safety_limits.max_archive_bytes
                ):
                    raise DownloadError(
                        "archive Content-Length exceeds the compressed safety limit"
                    )

                digest = hashlib.sha256()
                archive_size = 0
                while True:
                    try:
                        chunk = response.read(128 * 1024)
                    except (TimeoutError, OSError, http.client.HTTPException) as error:
                        raise TransientDownloadError(
                            f"archive transfer failed for {url}: {error}"
                        ) from error
                    if not chunk:
                        break
                    archive_size += len(chunk)
                    if archive_size > self.safety_limits.max_archive_bytes:
                        raise DownloadError(
                            "archive exceeds the compressed safety limit"
                        )
                    try:
                        temporary.write(chunk)
                    except OSError as error:
                        raise PublicationError(
                            f"cannot write temporary archive: {error}"
                        ) from error
                    digest.update(chunk)

                if archive_size == 0:
                    raise DownloadError("downloaded archive is empty")
                if expected_length is not None and archive_size != expected_length:
                    raise TransientDownloadError(
                        f"partial archive download from {url}: expected "
                        f"{expected_length} bytes, received {archive_size}"
                    )
                try:
                    temporary.flush()
                    os.fsync(temporary.fileno())
                except OSError as error:
                    raise PublicationError(
                        f"cannot flush temporary archive: {error}"
                    ) from error

                actual_checksum = digest.hexdigest()
                if actual_checksum != expected_checksum:
                    raise ChecksumError(
                        "downloaded archive SHA-256 does not match the "
                        "published checksum"
                    )
                metadata = _observed_http_metadata(response.headers)
            completed = True
            return temporary_path, actual_checksum, archive_size, metadata
        finally:
            if not completed:
                temporary_path.unlink(missing_ok=True)

    def _retry(
        self,
        url: str,
        operation: Callable[[], tuple],
    ) -> tuple:
        last_error: BaseException | None = None
        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                return operation()
            except TransientDownloadError as error:
                last_error = error
            except (
                TimeoutError,
                ConnectionError,
                OSError,
                http.client.HTTPException,
            ) as error:
                last_error = error

            if attempt < self.retries.max_attempts:
                delay = self.retries.backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "transient download failure for %s (attempt %d/%d); "
                    "retrying in %.2fs",
                    url,
                    attempt,
                    self.retries.max_attempts,
                    delay,
                )
                self.sleeper(delay)

        raise DownloadError(
            f"download failed after {self.retries.max_attempts} attempts: {url}"
        ) from last_error

    def _make_provenance(
        self,
        *,
        request: MonthlyKlineRequest,
        expected_checksum: str,
        actual_checksum: str,
        archive_size: int,
        archive_member_name: str,
        archive_metadata: Mapping[str, str],
        checksum_metadata: Mapping[str, str],
    ) -> ProvenanceRecord:
        retrieved_at = self.clock()
        if retrieved_at.tzinfo is None:
            raise ProvenanceError("injected clock must return a timezone-aware value")
        retrieved_at = retrieved_at.astimezone(UTC)
        return ProvenanceRecord(
            source_name=SOURCE_NAME,
            archive_url=archive_url(request),
            checksum_url=checksum_url(request),
            symbol=request.symbol,
            market=request.market,
            interval=request.interval,
            year=request.period.year,
            month=request.period.month,
            expected_sha256=expected_checksum,
            actual_sha256=actual_checksum,
            archive_size=archive_size,
            retrieved_at_utc=retrieved_at,
            source_timestamp_unit=request.timestamp_unit,
            schema_version=SCHEMA_VERSION,
            parser_version=PARSER_VERSION,
            dataset_terms_url=DATASET_TERMS_URL,
            archive_member_name=archive_member_name,
            archive_http_metadata=dict(archive_metadata),
            checksum_http_metadata=dict(checksum_metadata),
        )

    def _validate_existing_provenance(
        self,
        record: ProvenanceRecord,
        request: MonthlyKlineRequest,
        checksum: str,
        archive_size: int,
        member_name: str,
    ) -> None:
        expected = {
            "source_name": SOURCE_NAME,
            "archive_url": archive_url(request),
            "checksum_url": checksum_url(request),
            "symbol": request.symbol,
            "market": request.market,
            "interval": request.interval,
            "year": request.period.year,
            "month": request.period.month,
            "expected_sha256": checksum,
            "actual_sha256": checksum,
            "archive_size": archive_size,
            "source_timestamp_unit": request.timestamp_unit,
            "schema_version": SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "dataset_terms_url": DATASET_TERMS_URL,
            "archive_member_name": member_name,
        }
        mismatches = [
            name for name, value in expected.items() if getattr(record, name) != value
        ]
        if mismatches:
            raise ProvenanceError(
                "existing provenance conflicts in fields: "
                + ", ".join(sorted(mismatches))
            )
