"""Typed models and errors for historical market-data ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from string import hexdigits

SOURCE_NAME = "Binance Vision"
DATASET_TERMS_URL = (
    "https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md"
)
SCHEMA_VERSION = "binance-spot-kline-12-v1"
PARSER_VERSION = "1"


class IngestionError(Exception):
    """Base class for expected ingestion failures."""


class RequestValidationError(IngestionError):
    """The requested dataset is outside the approved ingestion scope."""


class DownloadError(IngestionError):
    """A remote resource could not be downloaded completely."""


class TransientDownloadError(DownloadError):
    """A download failed in a way that may succeed when retried."""


class ChecksumError(IngestionError):
    """A checksum is malformed or does not match its archive."""


class CorrectionConflictError(IngestionError):
    """A remote checksum conflicts with an already accepted local archive."""


class ArchiveValidationError(IngestionError):
    """A ZIP archive is malformed or unsafe."""


class CsvValidationError(IngestionError):
    """A kline CSV has an unsupported or malformed structure."""


class TimestampValidationError(CsvValidationError):
    """A source timestamp has an invalid unit, range, or ordering."""


class PublicationError(IngestionError):
    """Validated output could not be published safely."""


class ProvenanceError(IngestionError):
    """Provenance could not be validated or written safely."""


class TimestampUnit(StrEnum):
    """Timestamp units used by Binance Vision Spot archives."""

    MILLISECONDS = "milliseconds"
    MICROSECONDS = "microseconds"


@dataclass(frozen=True, order=True)
class YearMonth:
    """A validated calendar month in the approved historical period."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 2021 <= self.year <= 2025:
            raise RequestValidationError(
                f"year must be within the approved 2021-2025 period: {self.year}"
            )
        if not 1 <= self.month <= 12:
            raise RequestValidationError(
                f"month must be between 1 and 12: {self.month}"
            )

    def next(self) -> YearMonth:
        """Return the following approved calendar month."""
        if self.month == 12:
            return YearMonth(self.year + 1, 1)
        return YearMonth(self.year, self.month + 1)


@dataclass(frozen=True)
class MonthlyKlineRequest:
    """One approved Binance Vision monthly Spot kline archive request."""

    period: YearMonth
    symbol: str = "BTCUSDT"
    market: str = "spot"
    interval: str = "1h"

    def __post_init__(self) -> None:
        expected = ("BTCUSDT", "spot", "1h")
        actual = (self.symbol, self.market, self.interval)
        if actual != expected:
            raise RequestValidationError(
                "Milestone 2 supports only BTCUSDT Spot 1h; "
                f"received symbol={self.symbol!r}, market={self.market!r}, "
                f"interval={self.interval!r}"
            )

    @property
    def archive_filename(self) -> str:
        """Return the canonical monthly archive filename."""
        return (
            f"{self.symbol}-{self.interval}-"
            f"{self.period.year:04d}-{self.period.month:02d}.zip"
        )

    @property
    def csv_filename(self) -> str:
        """Return the only CSV member accepted in this archive."""
        return self.archive_filename.removesuffix(".zip") + ".csv"

    @property
    def timestamp_unit(self) -> TimestampUnit:
        """Return the documented source unit for this archive month."""
        if self.period.year < 2025:
            return TimestampUnit.MILLISECONDS
        return TimestampUnit.MICROSECONDS


@dataclass(frozen=True)
class SafetyLimits:
    """Conservative limits suitable for a monthly BTCUSDT 1h archive."""

    max_archive_bytes: int = 16 * 1024 * 1024
    max_uncompressed_bytes: int = 64 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_csv_rows: int = 1_000

    def __post_init__(self) -> None:
        if self.max_archive_bytes <= 0:
            raise ValueError("max_archive_bytes must be positive")
        if self.max_uncompressed_bytes <= 0:
            raise ValueError("max_uncompressed_bytes must be positive")
        if self.max_compression_ratio <= 0:
            raise ValueError("max_compression_ratio must be positive")
        if self.max_csv_rows <= 0:
            raise ValueError("max_csv_rows must be positive")


@dataclass(frozen=True)
class HttpTimeouts:
    """Separate connection and response-read timeout settings."""

    connect_seconds: float = 10.0
    read_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.connect_seconds <= 0 or self.read_seconds <= 0:
            raise ValueError("HTTP timeouts must be positive")


@dataclass(frozen=True)
class RetryPolicy:
    """A bounded retry policy for transient transport failures."""

    max_attempts: int = 3
    backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")


@dataclass(frozen=True)
class Kline:
    """One structurally validated Binance Spot kline."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: datetime
    quote_asset_volume: Decimal
    number_of_trades: int
    taker_buy_base_asset_volume: Decimal
    taker_buy_quote_asset_volume: Decimal
    ignore: Decimal

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC or self.close_time.tzinfo is not UTC:
            raise ValueError("kline timestamps must use datetime.UTC")


@dataclass(frozen=True)
class ArchiveValidation:
    """Summary produced after streaming validation of an archive."""

    member_name: str
    row_count: int
    timestamp_unit: TimestampUnit


@dataclass(frozen=True)
class ProvenanceRecord:
    """Observed source and integrity metadata for an accepted archive."""

    source_name: str
    archive_url: str
    checksum_url: str
    symbol: str
    market: str
    interval: str
    year: int
    month: int
    expected_sha256: str
    actual_sha256: str
    archive_size: int
    retrieved_at_utc: datetime
    source_timestamp_unit: TimestampUnit
    schema_version: str
    parser_version: str
    dataset_terms_url: str
    archive_member_name: str
    archive_http_metadata: Mapping[str, str] = field(default_factory=dict)
    checksum_http_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.retrieved_at_utc.tzinfo is not UTC:
            raise ValueError("retrieved_at_utc must use datetime.UTC")
        for field_name, digest in (
            ("expected_sha256", self.expected_sha256),
            ("actual_sha256", self.actual_sha256),
        ):
            if (
                len(digest) != 64
                or any(character not in hexdigits for character in digest)
                or digest != digest.lower()
            ):
                raise ValueError(
                    f"{field_name} must be a lowercase 64-character SHA-256"
                )
        if self.expected_sha256 != self.actual_sha256:
            raise ValueError("expected and actual SHA-256 values must match")
        if self.archive_size <= 0:
            raise ValueError("archive_size must be positive")
        if not 2021 <= self.year <= 2025:
            raise ValueError("year must be within the approved 2021-2025 period")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")


@dataclass(frozen=True)
class IngestionResult:
    """Paths and provenance returned only after complete ingestion."""

    archive_path: Path
    provenance_path: Path
    provenance: ProvenanceRecord
    reused_existing_archive: bool
