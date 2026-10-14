"""Verified monthly Parquet publication using in-memory DuckDB."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from quantforge.ingestion import (
    IngestionError,
    IngestionResult,
    Kline,
    MonthlyKlineRequest,
    iter_klines,
    read_provenance,
)
from quantforge.ingestion.models import ProvenanceRecord
from quantforge.quality import (
    VALIDATION_VERSION,
    MonthAssessment,
    validate_month,
)
from quantforge.storage.manifest import (
    read_manifest,
    serialize_manifest,
    serialize_validation_report,
    write_json_file,
)
from quantforge.storage.models import (
    STORAGE_SCHEMA_VERSION,
    DataQualityError,
    SourceVerificationError,
    StorageConflictError,
    StorageManifest,
    StoragePublicationError,
    StorageResult,
    StorageValidationError,
)

PARQUET_COLUMNS = (
    "symbol",
    "market",
    "interval",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
    "source_archive_sha256",
    "source_year",
    "source_month",
    "source_schema_version",
)

PARQUET_SCHEMA = (
    ("symbol", "VARCHAR"),
    ("market", "VARCHAR"),
    ("interval", "VARCHAR"),
    ("open_time", "TIMESTAMP WITH TIME ZONE"),
    ("open", "DECIMAL(38,18)"),
    ("high", "DECIMAL(38,18)"),
    ("low", "DECIMAL(38,18)"),
    ("close", "DECIMAL(38,18)"),
    ("volume", "DECIMAL(38,18)"),
    ("close_time", "TIMESTAMP WITH TIME ZONE"),
    ("quote_asset_volume", "DECIMAL(38,18)"),
    ("number_of_trades", "BIGINT"),
    ("taker_buy_base_asset_volume", "DECIMAL(38,18)"),
    ("taker_buy_quote_asset_volume", "DECIMAL(38,18)"),
    ("ignore", "DECIMAL(38,18)"),
    ("source_archive_sha256", "VARCHAR"),
    ("source_year", "INTEGER"),
    ("source_month", "INTEGER"),
    ("source_schema_version", "VARCHAR"),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_VERIFICATION_SELECT = """
    symbol,
    market,
    interval,
    epoch_us(open_time),
    open,
    high,
    low,
    close,
    volume,
    epoch_us(close_time),
    quote_asset_volume,
    number_of_trades,
    taker_buy_base_asset_volume,
    taker_buy_quote_asset_volume,
    ignore,
    source_archive_sha256,
    source_year,
    source_month,
    source_schema_version
"""

_CREATE_TABLE_SQL = """
CREATE TABLE klines (
    symbol VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    open DECIMAL(38,18) NOT NULL,
    high DECIMAL(38,18) NOT NULL,
    low DECIMAL(38,18) NOT NULL,
    close DECIMAL(38,18) NOT NULL,
    volume DECIMAL(38,18) NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    quote_asset_volume DECIMAL(38,18) NOT NULL,
    number_of_trades BIGINT NOT NULL,
    taker_buy_base_asset_volume DECIMAL(38,18) NOT NULL,
    taker_buy_quote_asset_volume DECIMAL(38,18) NOT NULL,
    ignore DECIMAL(38,18) NOT NULL,
    source_archive_sha256 VARCHAR NOT NULL,
    source_year INTEGER NOT NULL,
    source_month INTEGER NOT NULL,
    source_schema_version VARCHAR NOT NULL
)
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise StorageValidationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _sql_string(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _open_connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def _epoch_microseconds(value: datetime) -> int:
    delta = value - _EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class DuckDBParquetStore:
    """Create and verify canonical monthly Parquet partitions.

    The raw archive and its on-disk M2 provenance are always reverified and
    reparsed. No caller-provided observations or validation result are accepted.
    """

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        publisher: Callable[[Path, Path], None] = os.rename,
    ) -> None:
        self._root = root
        self._clock = clock
        self._publisher = publisher

    def partition_path(self, request: MonthlyKlineRequest) -> Path:
        """Return the deterministic canonical directory for one request."""
        return (
            self._root
            / "binance-vision"
            / request.market
            / "klines"
            / f"symbol={request.symbol}"
            / f"interval={request.interval}"
            / f"year={request.period.year:04d}"
            / f"month={request.period.month:02d}"
        )

    def process_month(
        self,
        request: MonthlyKlineRequest,
        ingestion: IngestionResult,
    ) -> StorageResult:
        """Verify source lineage, validate, write, read back, and publish."""
        provenance, provenance_sha, observations = self._verify_source(
            request, ingestion
        )
        assessment = validate_month(
            request,
            observations,
            provenance,
        )
        if not assessment.report.is_valid:
            raise DataQualityError(assessment.report)

        final_path = self.partition_path(request)
        if final_path.exists():
            return self._verify_existing(
                final_path,
                request,
                provenance,
                provenance_sha,
                assessment,
            )

        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StoragePublicationError(
                f"cannot create partition parent {final_path.parent}: {error}"
            ) from error
        try:
            temporary_path = Path(
                tempfile.mkdtemp(
                    prefix=f".month={request.period.month:02d}.",
                    suffix=".tmp",
                    dir=final_path.parent,
                )
            )
        except OSError as error:
            raise StoragePublicationError(
                f"cannot create temporary partition beside {final_path}: {error}"
            ) from error

        try:
            self._build_temporary_partition(
                temporary_path,
                request,
                ingestion,
                provenance,
                provenance_sha,
                assessment,
            )
            self._verify_source_files_unchanged(
                ingestion,
                provenance,
                provenance_sha,
            )
            try:
                self._publisher(temporary_path, final_path)
            except OSError as error:
                if final_path.exists():
                    existing = self._verify_existing(
                        final_path,
                        request,
                        provenance,
                        provenance_sha,
                        assessment,
                    )
                    self._fsync_directory(final_path.parent)
                    return existing
                raise StoragePublicationError(
                    f"cannot publish partition {final_path}: {error}"
                ) from error
            if not final_path.is_dir():
                raise StoragePublicationError(
                    "partition publisher returned without creating the final directory"
                )
            verified = self._verify_existing(
                final_path,
                request,
                provenance,
                provenance_sha,
                assessment,
            )
            self._fsync_directory(final_path.parent)
            return StorageResult(
                partition_path=final_path,
                parquet_path=verified.parquet_path,
                manifest_path=final_path / "manifest.json",
                validation_path=final_path / "validation.json",
                manifest=verified.manifest,
                validation_report=assessment.report,
                reused_existing_partition=False,
            )
        finally:
            shutil.rmtree(temporary_path, ignore_errors=True)

    def _verify_source(
        self,
        request: MonthlyKlineRequest,
        ingestion: IngestionResult,
    ) -> tuple[ProvenanceRecord, str, tuple[Kline, ...]]:
        expected_archive_name = request.archive_filename
        expected_provenance_name = (
            f"{request.archive_filename.removesuffix('.zip')}.provenance.json"
        )
        if ingestion.archive_path.name != expected_archive_name:
            raise SourceVerificationError(
                "raw archive filename does not match the request identity"
            )
        if ingestion.provenance_path.name != expected_provenance_name:
            raise SourceVerificationError(
                "provenance filename does not match the request identity"
            )
        if not ingestion.archive_path.is_file():
            raise SourceVerificationError(
                f"raw archive is missing: {ingestion.archive_path}"
            )
        if not ingestion.provenance_path.is_file():
            raise SourceVerificationError(
                f"source provenance is missing: {ingestion.provenance_path}"
            )

        try:
            provenance = read_provenance(ingestion.provenance_path)
        except (IngestionError, OSError) as error:
            raise SourceVerificationError(
                f"source provenance is invalid: {error}"
            ) from error
        if provenance != ingestion.provenance:
            raise SourceVerificationError(
                "caller-provided provenance differs from the on-disk record"
            )

        try:
            source_sha = _sha256_file(ingestion.archive_path)
            provenance_sha = _sha256_file(ingestion.provenance_path)
        except StorageValidationError as error:
            raise SourceVerificationError(str(error)) from error
        if source_sha != provenance.expected_sha256:
            raise SourceVerificationError(
                "raw archive SHA-256 does not match source provenance"
            )
        if provenance.actual_sha256 != source_sha:
            raise SourceVerificationError(
                "source provenance expected and actual SHA-256 values disagree"
            )
        try:
            archive_size = ingestion.archive_path.stat().st_size
        except OSError as error:
            raise SourceVerificationError(
                f"cannot stat raw archive: {error}"
            ) from error
        if provenance.archive_size != archive_size:
            raise SourceVerificationError(
                "raw archive size does not match source provenance"
            )

        try:
            observations = tuple(iter_klines(ingestion.archive_path, request))
        except (IngestionError, OSError) as error:
            raise SourceVerificationError(
                f"raw archive cannot be safely reparsed: {error}"
            ) from error
        self._verify_source_files_unchanged(
            ingestion,
            provenance,
            provenance_sha,
        )
        return provenance, provenance_sha, observations

    @staticmethod
    def _verify_source_files_unchanged(
        ingestion: IngestionResult,
        provenance: ProvenanceRecord,
        provenance_sha: str,
    ) -> None:
        try:
            current_archive_sha = _sha256_file(ingestion.archive_path)
            current_provenance_sha = _sha256_file(ingestion.provenance_path)
            current_archive_size = ingestion.archive_path.stat().st_size
        except (OSError, StorageValidationError) as error:
            raise SourceVerificationError(
                f"cannot reverify source files: {error}"
            ) from error
        if (
            current_archive_sha != provenance.actual_sha256
            or current_archive_size != provenance.archive_size
            or current_provenance_sha != provenance_sha
        ):
            raise SourceVerificationError(
                "source archive or provenance changed during processing"
            )

    def _build_temporary_partition(
        self,
        directory: Path,
        request: MonthlyKlineRequest,
        ingestion: IngestionResult,
        provenance: ProvenanceRecord,
        provenance_sha: str,
        assessment: MonthAssessment,
    ) -> StorageResult:
        parquet_name = (
            f"{request.symbol}-{request.interval}-"
            f"{request.period.year:04d}-{request.period.month:02d}.parquet"
        )
        parquet_path = directory / parquet_name
        self._write_parquet(parquet_path, assessment.observations, provenance)
        self._verify_parquet(
            parquet_path,
            assessment.observations,
            request,
            provenance,
        )

        validation_content = serialize_validation_report(assessment.report)
        validation_path = directory / "validation.json"
        write_json_file(validation_path, validation_content)
        validation_sha = _sha256_file(validation_path)

        created_at = self._clock()
        if created_at.tzinfo is None:
            raise StorageValidationError("injected storage clock returned a naive time")
        created_at = created_at.astimezone(UTC)
        parquet_sha = _sha256_file(parquet_path)
        try:
            parquet_size = parquet_path.stat().st_size
        except OSError as error:
            raise StorageValidationError(
                f"cannot stat generated Parquet: {error}"
            ) from error

        observations = assessment.observations
        manifest = StorageManifest(
            symbol=request.symbol,
            market=request.market,
            interval=request.interval,
            year=request.period.year,
            month=request.period.month,
            source_archive_filename=ingestion.archive_path.name,
            source_archive_sha256=provenance.actual_sha256,
            source_provenance_filename=ingestion.provenance_path.name,
            source_provenance_sha256=provenance_sha,
            source_schema_version=provenance.schema_version,
            source_parser_version=provenance.parser_version,
            validation_version=assessment.report.validation_version,
            storage_schema_version=STORAGE_SCHEMA_VERSION,
            duckdb_version=duckdb.__version__,
            parquet_filename=parquet_name,
            parquet_sha256=parquet_sha,
            parquet_size=parquet_size,
            observation_count=len(observations),
            first_open_time=observations[0].open_time,
            last_open_time=observations[-1].open_time,
            warning_count=len(assessment.report.warnings),
            validation_sha256=validation_sha,
            created_at_utc=created_at,
        )
        manifest_path = directory / "manifest.json"
        write_json_file(manifest_path, serialize_manifest(manifest))
        self._fsync_directory(directory)
        return StorageResult(
            partition_path=directory,
            parquet_path=parquet_path,
            manifest_path=manifest_path,
            validation_path=validation_path,
            manifest=manifest,
            validation_report=assessment.report,
            reused_existing_partition=False,
        )

    def _write_parquet(
        self,
        path: Path,
        observations: Sequence[Kline],
        provenance: ProvenanceRecord,
    ) -> None:
        placeholders = ", ".join("?" for _ in PARQUET_COLUMNS)
        rows = [
            self._expected_row(observation, provenance) for observation in observations
        ]
        try:
            with _open_connection() as connection:
                connection.execute(_CREATE_TABLE_SQL)
                connection.executemany(
                    f"INSERT INTO klines VALUES ({placeholders})",
                    rows,
                )
                connection.execute(
                    "COPY (SELECT * FROM klines ORDER BY open_time) "
                    f"TO {_sql_string(path)} "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
        except duckdb.Error as error:
            raise StorageValidationError(
                f"DuckDB could not write Parquet: {error}"
            ) from error

    def _verify_parquet(
        self,
        path: Path,
        observations: Sequence[Kline],
        request: MonthlyKlineRequest,
        provenance: ProvenanceRecord,
    ) -> None:
        parquet = _sql_string(path)
        try:
            with _open_connection() as connection:
                described = connection.execute(
                    "DESCRIBE SELECT * FROM "
                    f"read_parquet({parquet}, hive_partitioning = false)"
                ).fetchall()
                actual_schema = tuple((row[0], row[1]) for row in described)
                if actual_schema != PARQUET_SCHEMA:
                    raise StorageValidationError(
                        f"Parquet schema mismatch: {actual_schema!r}"
                    )
                rows = connection.execute(
                    f"SELECT {_VERIFICATION_SELECT} FROM "
                    f"read_parquet({parquet}, hive_partitioning = false) "
                    "ORDER BY open_time"
                ).fetchall()
        except duckdb.Error as error:
            raise StorageValidationError(
                f"DuckDB could not read generated Parquet: {error}"
            ) from error

        if len(rows) != len(observations):
            raise StorageValidationError(
                f"Parquet row count {len(rows)} does not match "
                f"validated count {len(observations)}"
            )
        for index, (actual, observation) in enumerate(
            zip(rows, observations, strict=True)
        ):
            expected = list(self._expected_row(observation, provenance))
            expected[3] = _epoch_microseconds(observation.open_time)
            expected[9] = _epoch_microseconds(observation.close_time)
            if len(actual) != len(expected):
                raise StorageValidationError(
                    f"Parquet row {index} has an unexpected column count"
                )
            for column, actual_value, expected_value in zip(
                PARQUET_COLUMNS,
                actual,
                expected,
                strict=True,
            ):
                if actual_value != expected_value:
                    raise StorageValidationError(
                        f"Parquet row {index} column {column} changed during round-trip"
                    )
            if actual[0:3] != (request.symbol, request.market, request.interval):
                raise StorageValidationError(
                    f"Parquet row {index} has inconsistent request identity"
                )

    @staticmethod
    def _expected_row(
        observation: Kline,
        provenance: ProvenanceRecord,
    ) -> tuple[Any, ...]:
        return (
            provenance.symbol,
            provenance.market,
            provenance.interval,
            observation.open_time,
            observation.open,
            observation.high,
            observation.low,
            observation.close,
            observation.volume,
            observation.close_time,
            observation.quote_asset_volume,
            observation.number_of_trades,
            observation.taker_buy_base_asset_volume,
            observation.taker_buy_quote_asset_volume,
            observation.ignore,
            provenance.actual_sha256,
            provenance.year,
            provenance.month,
            provenance.schema_version,
        )

    def _verify_existing(
        self,
        directory: Path,
        request: MonthlyKlineRequest,
        provenance: ProvenanceRecord,
        provenance_sha: str,
        assessment: MonthAssessment,
    ) -> StorageResult:
        if not directory.is_dir():
            raise StorageValidationError(
                f"processed partition path is not a directory: {directory}"
            )
        manifest_path = directory / "manifest.json"
        validation_path = directory / "validation.json"
        if not manifest_path.is_file() or not validation_path.is_file():
            raise StorageValidationError("existing processed partition is incomplete")
        manifest = read_manifest(manifest_path)

        identity = (
            manifest.symbol,
            manifest.market,
            manifest.interval,
            manifest.year,
            manifest.month,
        )
        expected_identity = (
            request.symbol,
            request.market,
            request.interval,
            request.period.year,
            request.period.month,
        )
        if identity != expected_identity:
            raise StorageConflictError(
                "existing partition has a different request identity"
            )
        if manifest.source_archive_sha256 != provenance.actual_sha256:
            raise StorageConflictError(
                "existing partition was produced from a different source archive"
            )
        if manifest.source_provenance_sha256 != provenance_sha:
            raise StorageConflictError(
                "existing partition was produced from different source provenance"
            )
        if (
            manifest.storage_schema_version != STORAGE_SCHEMA_VERSION
            or manifest.validation_version != VALIDATION_VERSION
            or manifest.duckdb_version != duckdb.__version__
        ):
            raise StorageConflictError(
                "existing partition uses incompatible processing versions"
            )
        if (
            manifest.source_schema_version != provenance.schema_version
            or manifest.source_parser_version != provenance.parser_version
        ):
            raise StorageConflictError(
                "existing partition uses incompatible source parser versions"
            )
        if (
            manifest.source_archive_filename != request.archive_filename
            or manifest.source_provenance_filename
            != f"{request.archive_filename.removesuffix('.zip')}.provenance.json"
        ):
            raise StorageConflictError(
                "existing partition refers to unexpected source filenames"
            )

        parquet_path = directory / manifest.parquet_filename
        expected_parquet_name = (
            f"{request.symbol}-{request.interval}-"
            f"{request.period.year:04d}-{request.period.month:02d}.parquet"
        )
        if manifest.parquet_filename != expected_parquet_name:
            raise StorageValidationError(
                "existing manifest names an unexpected Parquet file"
            )
        expected_entries = {
            "manifest.json",
            "validation.json",
            expected_parquet_name,
        }
        try:
            actual_entries = {entry.name for entry in directory.iterdir()}
        except OSError as error:
            raise StorageValidationError(
                f"cannot inspect existing partition directory: {error}"
            ) from error
        if actual_entries != expected_entries:
            raise StorageValidationError(
                "existing partition contains missing or unexpected entries"
            )
        if not parquet_path.is_file():
            raise StorageValidationError("existing manifest's Parquet file is missing")
        try:
            parquet_size = parquet_path.stat().st_size
        except OSError as error:
            raise StorageValidationError(
                f"cannot stat existing Parquet file: {error}"
            ) from error
        if (
            _sha256_file(parquet_path) != manifest.parquet_sha256
            or parquet_size != manifest.parquet_size
        ):
            raise StorageValidationError(
                "existing Parquet file does not match its manifest"
            )

        expected_validation = serialize_validation_report(assessment.report)
        try:
            actual_validation = validation_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise StorageValidationError(
                f"cannot read existing validation report: {error}"
            ) from error
        if actual_validation != expected_validation:
            raise StorageValidationError(
                "existing validation report differs from current validation"
            )
        if _sha256_file(validation_path) != manifest.validation_sha256:
            raise StorageValidationError(
                "existing validation report does not match its manifest"
            )
        if (
            manifest.observation_count != len(assessment.observations)
            or manifest.warning_count != len(assessment.report.warnings)
            or manifest.first_open_time != assessment.observations[0].open_time
            or manifest.last_open_time != assessment.observations[-1].open_time
        ):
            raise StorageValidationError(
                "existing manifest coverage does not match validated observations"
            )
        self._verify_parquet(
            parquet_path,
            assessment.observations,
            request,
            provenance,
        )
        return StorageResult(
            partition_path=directory,
            parquet_path=parquet_path,
            manifest_path=manifest_path,
            validation_path=validation_path,
            manifest=manifest,
            validation_report=assessment.report,
            reused_existing_partition=True,
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StoragePublicationError(
                f"cannot fsync directory {path}: {error}"
            ) from error
