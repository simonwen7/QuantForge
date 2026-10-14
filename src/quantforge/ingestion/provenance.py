"""Deterministic provenance serialization and atomic sidecar publication."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.ingestion.models import (
    ProvenanceError,
    ProvenanceRecord,
    TimestampUnit,
)


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def provenance_to_dict(record: ProvenanceRecord) -> dict[str, Any]:
    """Convert provenance to a stable JSON-compatible mapping."""
    payload: dict[str, Any] = {
        "actual_sha256": record.actual_sha256,
        "archive_member_name": record.archive_member_name,
        "archive_size": record.archive_size,
        "archive_url": record.archive_url,
        "checksum_url": record.checksum_url,
        "dataset_terms_url": record.dataset_terms_url,
        "expected_sha256": record.expected_sha256,
        "interval": record.interval,
        "market": record.market,
        "month": record.month,
        "parser_version": record.parser_version,
        "retrieved_at_utc": _format_utc(record.retrieved_at_utc),
        "schema_version": record.schema_version,
        "source_name": record.source_name,
        "source_timestamp_unit": record.source_timestamp_unit.value,
        "symbol": record.symbol,
        "year": record.year,
    }
    if record.archive_http_metadata:
        payload["archive_http_metadata"] = dict(record.archive_http_metadata)
    if record.checksum_http_metadata:
        payload["checksum_http_metadata"] = dict(record.checksum_http_metadata)
    return payload


def serialize_provenance(record: ProvenanceRecord) -> str:
    """Serialize provenance deterministically with a final newline."""
    return (
        json.dumps(
            provenance_to_dict(record),
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProvenanceError("retrieved_at_utc must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ProvenanceError("retrieved_at_utc is not a valid datetime") from error
    if parsed.tzinfo is not UTC:
        parsed = parsed.astimezone(UTC)
    return parsed


def _string_mapping(value: object, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProvenanceError(f"{field_name} must be a JSON object")
    if not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ProvenanceError(f"{field_name} keys and values must be strings")
    return dict(value)


def provenance_from_dict(payload: object) -> ProvenanceRecord:
    """Validate and construct a provenance record from decoded JSON."""
    if not isinstance(payload, dict):
        raise ProvenanceError("provenance root must be a JSON object")
    required = {
        "actual_sha256",
        "archive_member_name",
        "archive_size",
        "archive_url",
        "checksum_url",
        "dataset_terms_url",
        "expected_sha256",
        "interval",
        "market",
        "month",
        "parser_version",
        "retrieved_at_utc",
        "schema_version",
        "source_name",
        "source_timestamp_unit",
        "symbol",
        "year",
    }
    optional = {"archive_http_metadata", "checksum_http_metadata"}
    missing = required - payload.keys()
    unknown = payload.keys() - required - optional
    if missing:
        raise ProvenanceError(
            f"provenance is missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise ProvenanceError(f"provenance contains unknown fields: {sorted(unknown)}")

    string_fields = required - {
        "archive_size",
        "month",
        "retrieved_at_utc",
        "source_timestamp_unit",
        "year",
    }
    for field_name in string_fields:
        if not isinstance(payload[field_name], str):
            raise ProvenanceError(f"{field_name} must be a string")
    for field_name in ("archive_size", "month", "year"):
        if not isinstance(payload[field_name], int):
            raise ProvenanceError(f"{field_name} must be an integer")

    try:
        timestamp_unit = TimestampUnit(payload["source_timestamp_unit"])
    except (TypeError, ValueError) as error:
        raise ProvenanceError("source_timestamp_unit is not recognized") from error

    try:
        return ProvenanceRecord(
            source_name=payload["source_name"],
            archive_url=payload["archive_url"],
            checksum_url=payload["checksum_url"],
            symbol=payload["symbol"],
            market=payload["market"],
            interval=payload["interval"],
            year=payload["year"],
            month=payload["month"],
            expected_sha256=payload["expected_sha256"],
            actual_sha256=payload["actual_sha256"],
            archive_size=payload["archive_size"],
            retrieved_at_utc=_parse_utc(payload["retrieved_at_utc"]),
            source_timestamp_unit=timestamp_unit,
            schema_version=payload["schema_version"],
            parser_version=payload["parser_version"],
            dataset_terms_url=payload["dataset_terms_url"],
            archive_member_name=payload["archive_member_name"],
            archive_http_metadata=_string_mapping(
                payload.get("archive_http_metadata"),
                "archive_http_metadata",
            ),
            checksum_http_metadata=_string_mapping(
                payload.get("checksum_http_metadata"),
                "checksum_http_metadata",
            ),
        )
    except ValueError as error:
        raise ProvenanceError(f"invalid provenance values: {error}") from error


def read_provenance(path: Path) -> ProvenanceRecord:
    """Read and strictly validate an existing provenance sidecar."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read provenance {path}: {error}") from error
    return provenance_from_dict(payload)


def write_provenance_atomic(path: Path, record: ProvenanceRecord) -> None:
    """Publish provenance atomically without overwriting an existing record."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ProvenanceError(
            f"cannot create provenance directory {path.parent}: {error}"
        ) from error
    if path.exists():
        existing = read_provenance(path)
        if existing != record:
            raise ProvenanceError(
                f"existing provenance conflicts with the accepted archive: {path}"
            )
        return

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".part",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialize_provenance(record))
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            existing = read_provenance(path)
            if existing != record:
                raise ProvenanceError(
                    f"concurrent provenance publication conflict: {path}"
                ) from None
    except ProvenanceError:
        raise
    except OSError as error:
        raise ProvenanceError(
            f"cannot publish provenance atomically at {path}: {error}"
        ) from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
