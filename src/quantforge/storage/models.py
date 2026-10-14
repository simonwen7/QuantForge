"""Typed models and errors for validated analytical storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quantforge.quality.models import ValidationReport

STORAGE_SCHEMA_VERSION = "1"


class StorageError(Exception):
    """Base class for expected processed-storage failures."""


class SourceVerificationError(StorageError):
    """The raw archive or its M2 provenance cannot be trusted."""


class DataQualityError(StorageError):
    """Current validation rules rejected the source observations."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(
            f"data quality validation failed with {len(report.errors)} error(s)"
        )


class StorageConflictError(StorageError):
    """An existing partition conflicts with the requested source or schema."""


class StorageValidationError(StorageError):
    """Generated or existing processed data failed verification."""


class StoragePublicationError(StorageError):
    """A complete partition could not be published safely."""


@dataclass(frozen=True)
class StorageManifest:
    """Deterministic lineage and integrity metadata for one partition."""

    symbol: str
    market: str
    interval: str
    year: int
    month: int
    source_archive_filename: str
    source_archive_sha256: str
    source_provenance_filename: str
    source_provenance_sha256: str
    source_schema_version: str
    source_parser_version: str
    validation_version: str
    storage_schema_version: str
    duckdb_version: str
    parquet_filename: str
    parquet_sha256: str
    parquet_size: int
    observation_count: int
    first_open_time: datetime
    last_open_time: datetime
    warning_count: int
    validation_sha256: str
    created_at_utc: datetime


@dataclass(frozen=True)
class StorageResult:
    """A verified processed partition returned after successful publication."""

    partition_path: Path
    parquet_path: Path
    manifest_path: Path
    validation_path: Path
    manifest: StorageManifest
    validation_report: ValidationReport
    reused_existing_partition: bool
