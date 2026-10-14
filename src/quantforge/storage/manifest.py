"""Deterministic JSON metadata for processed monthly partitions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.quality.models import ValidationIssue, ValidationReport
from quantforge.storage.models import StorageManifest, StorageValidationError


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise StorageValidationError("manifest timestamps must be timezone-aware")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StorageValidationError(f"{field_name} must be an ISO-8601 UTC string")
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as error:
        raise StorageValidationError(f"{field_name} is invalid") from error


def manifest_to_dict(manifest: StorageManifest) -> dict[str, Any]:
    """Convert a manifest to stable JSON-compatible values."""
    return {
        "created_at_utc": _format_utc(manifest.created_at_utc),
        "duckdb_version": manifest.duckdb_version,
        "first_open_time": _format_utc(manifest.first_open_time),
        "interval": manifest.interval,
        "last_open_time": _format_utc(manifest.last_open_time),
        "market": manifest.market,
        "month": manifest.month,
        "observation_count": manifest.observation_count,
        "parquet_filename": manifest.parquet_filename,
        "parquet_sha256": manifest.parquet_sha256,
        "parquet_size": manifest.parquet_size,
        "source_archive_filename": manifest.source_archive_filename,
        "source_archive_sha256": manifest.source_archive_sha256,
        "source_parser_version": manifest.source_parser_version,
        "source_provenance_filename": manifest.source_provenance_filename,
        "source_provenance_sha256": manifest.source_provenance_sha256,
        "source_schema_version": manifest.source_schema_version,
        "storage_schema_version": manifest.storage_schema_version,
        "symbol": manifest.symbol,
        "validation_sha256": manifest.validation_sha256,
        "validation_version": manifest.validation_version,
        "warning_count": manifest.warning_count,
        "year": manifest.year,
    }


def serialize_manifest(manifest: StorageManifest) -> str:
    """Serialize a storage manifest deterministically."""
    return (
        json.dumps(
            manifest_to_dict(manifest),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def _issue_to_dict(issue: ValidationIssue) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": issue.code.value,
        "context": dict(issue.context),
        "message": issue.message,
        "severity": issue.severity.value,
    }
    if issue.row_index is not None:
        payload["row_index"] = issue.row_index
    if issue.open_time is not None:
        payload["open_time"] = _format_utc(issue.open_time)
    return payload


def serialize_validation_report(report: ValidationReport) -> str:
    """Serialize all errors and warnings without changing their order."""
    payload = {
        "error_count": len(report.errors),
        "expected_rows": report.expected_rows,
        "issues": [_issue_to_dict(issue) for issue in report.issues],
        "observed_rows": report.observed_rows,
        "request": {
            "interval": report.request.interval,
            "market": report.request.market,
            "month": report.request.period.month,
            "symbol": report.request.symbol,
            "year": report.request.period.year,
        },
        "source_sha256": report.source_sha256,
        "validation_version": report.validation_version,
        "warning_count": len(report.warnings),
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_json_file(path: Path, content: str) -> None:
    """Write and fsync JSON inside an unpublished temporary directory."""
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise StorageValidationError(
            f"cannot write metadata {path}: {error}"
        ) from error


def read_manifest(path: Path) -> StorageManifest:
    """Strictly read an existing processed-partition manifest."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageValidationError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(payload, dict):
        raise StorageValidationError("manifest root must be a JSON object")

    expected_fields = {
        "created_at_utc",
        "duckdb_version",
        "first_open_time",
        "interval",
        "last_open_time",
        "market",
        "month",
        "observation_count",
        "parquet_filename",
        "parquet_sha256",
        "parquet_size",
        "source_archive_filename",
        "source_archive_sha256",
        "source_parser_version",
        "source_provenance_filename",
        "source_provenance_sha256",
        "source_schema_version",
        "storage_schema_version",
        "symbol",
        "validation_sha256",
        "validation_version",
        "warning_count",
        "year",
    }
    missing = expected_fields - payload.keys()
    unknown = payload.keys() - expected_fields
    if missing or unknown:
        raise StorageValidationError(
            f"manifest fields are invalid; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )

    integer_fields = {
        "month",
        "observation_count",
        "parquet_size",
        "warning_count",
        "year",
    }
    for field_name in integer_fields:
        if not isinstance(payload[field_name], int):
            raise StorageValidationError(f"{field_name} must be an integer")
    for field_name in (
        expected_fields
        - integer_fields
        - {
            "created_at_utc",
            "first_open_time",
            "last_open_time",
        }
    ):
        if not isinstance(payload[field_name], str):
            raise StorageValidationError(f"{field_name} must be a string")

    try:
        manifest = StorageManifest(
            symbol=payload["symbol"],
            market=payload["market"],
            interval=payload["interval"],
            year=payload["year"],
            month=payload["month"],
            source_archive_filename=payload["source_archive_filename"],
            source_archive_sha256=payload["source_archive_sha256"],
            source_provenance_filename=payload["source_provenance_filename"],
            source_provenance_sha256=payload["source_provenance_sha256"],
            source_schema_version=payload["source_schema_version"],
            source_parser_version=payload["source_parser_version"],
            validation_version=payload["validation_version"],
            storage_schema_version=payload["storage_schema_version"],
            duckdb_version=payload["duckdb_version"],
            parquet_filename=payload["parquet_filename"],
            parquet_sha256=payload["parquet_sha256"],
            parquet_size=payload["parquet_size"],
            observation_count=payload["observation_count"],
            first_open_time=_parse_utc(payload["first_open_time"], "first_open_time"),
            last_open_time=_parse_utc(payload["last_open_time"], "last_open_time"),
            warning_count=payload["warning_count"],
            validation_sha256=payload["validation_sha256"],
            created_at_utc=_parse_utc(payload["created_at_utc"], "created_at_utc"),
        )
    except (TypeError, ValueError) as error:
        raise StorageValidationError(f"manifest values are invalid: {error}") from error

    for field_name in (
        "source_archive_sha256",
        "source_provenance_sha256",
        "parquet_sha256",
        "validation_sha256",
    ):
        value = getattr(manifest, field_name)
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise StorageValidationError(f"{field_name} is not a lowercase SHA-256")
    if manifest.parquet_size <= 0 or manifest.observation_count <= 0:
        raise StorageValidationError(
            "manifest parquet size and observation count must be positive"
        )
    return manifest
