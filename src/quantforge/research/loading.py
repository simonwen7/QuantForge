"""Load research observations only through verified M3 storage."""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from quantforge.ingestion.models import IngestionResult, MonthlyKlineRequest, YearMonth
from quantforge.quality.models import ValidationIssue
from quantforge.research.experiment import ResearchConfiguration
from quantforge.research.models import (
    CoverageError,
    CoverageGap,
    CoveragePolicy,
    HoldoutAccessError,
    PartitionLineage,
    PriceObservation,
    ResearchAccessMode,
    ResearchDataError,
    ResearchDataset,
)
from quantforge.storage.duckdb_parquet import DuckDBParquetStore

_HOUR = timedelta(hours=1)
_READ_PARTITION_SQL = """
SELECT
    symbol,
    market,
    interval,
    epoch_us(open_time),
    epoch_us(close_time),
    close,
    source_archive_sha256,
    source_year,
    source_month,
    source_schema_version
FROM read_parquet(?, hive_partitioning = false)
ORDER BY open_time
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ResearchDataError(
            f"cannot hash research source {path}: {error}"
        ) from error
    return digest.hexdigest()


def _period_follows(left: YearMonth, right: YearMonth) -> bool:
    if left.month == 12:
        return right.year == left.year + 1 and right.month == 1
    return right.year == left.year and right.month == left.month + 1


def _from_epoch_microseconds(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchDataError("DuckDB epoch_us result was not an integer")
    seconds, microseconds = divmod(value, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=microseconds)


def _expected_close_time(open_time: datetime) -> datetime:
    tick = (
        timedelta(milliseconds=1)
        if open_time.year < 2025
        else timedelta(microseconds=1)
    )
    return open_time + _HOUR - tick


class ResearchDataLoader:
    """Materialize verified observations without accepting raw Parquet paths."""

    def __init__(self, store: DuckDBParquetStore) -> None:
        self._store = store

    def load(
        self,
        sources: Sequence[tuple[MonthlyKlineRequest, IngestionResult]],
        configuration: ResearchConfiguration,
    ) -> ResearchDataset:
        """Revalidate every month, then enforce coverage and availability rules."""
        self._enforce_access(sources, configuration)
        self._enforce_requested_shape(sources, configuration)
        observations: list[PriceObservation] = []
        lineage: list[PartitionLineage] = []
        warnings: list[ValidationIssue] = []
        for request, ingestion in sources:
            stored = self._store.process_month(request, ingestion)
            observations.extend(
                self._read_verified_partition(request, ingestion, stored)
            )
            lineage.append(self._lineage(stored))
            warnings.extend(stored.validation_report.warnings)
        return self._assemble(
            tuple(observations), tuple(lineage), tuple(warnings), configuration
        )

    def _enforce_access(
        self,
        sources: Sequence[tuple[MonthlyKlineRequest, IngestionResult]],
        configuration: ResearchConfiguration,
    ) -> None:
        requested = [request.period for request, _ in sources]
        requested.extend(configuration.expected_periods)
        includes_holdout = any(period.year >= 2025 for period in requested)
        if configuration.access_mode is ResearchAccessMode.DEVELOPMENT:
            if configuration.final_evaluation_authorized:
                raise HoldoutAccessError(
                    "development mode cannot authorize final-holdout access"
                )
            if includes_holdout:
                raise HoldoutAccessError(
                    "development mode cannot materialize final-holdout observations"
                )
            return
        if configuration.access_mode is ResearchAccessMode.FINAL_EVALUATION:
            if not configuration.final_evaluation_authorized:
                raise HoldoutAccessError(
                    "final evaluation requires explicit authorization"
                )
            return
        raise ResearchDataError(
            f"unsupported research access mode: {configuration.access_mode!r}"
        )

    def _enforce_requested_shape(
        self,
        sources: Sequence[tuple[MonthlyKlineRequest, IngestionResult]],
        configuration: ResearchConfiguration,
    ) -> None:
        expected = tuple(configuration.expected_periods)
        source_periods = tuple(request.period for request, _ in sources)
        if not expected:
            raise CoverageError("expected research coverage cannot be empty")
        if not source_periods:
            raise CoverageError("at least one verified monthly source is required")
        self._require_chronological_unique(expected, "expected periods")
        self._require_chronological_unique(source_periods, "sources")
        unexpected = [period for period in source_periods if period not in expected]
        if unexpected:
            raise CoverageError("sources contain months outside the expected coverage")
        if configuration.coverage_policy is CoveragePolicy.STRICT:
            if source_periods != expected:
                raise CoverageError(
                    "strict coverage requires every expected month exactly once"
                )
            self._require_contiguous(expected)
        elif configuration.coverage_policy is not CoveragePolicy.EXPLICIT_PARTIAL:
            raise CoverageError(
                f"unsupported coverage policy: {configuration.coverage_policy!r}"
            )

    @staticmethod
    def _require_chronological_unique(
        periods: Sequence[YearMonth],
        label: str,
    ) -> None:
        if tuple(sorted(periods)) != tuple(periods):
            raise CoverageError(f"{label} must be strictly chronological")
        if len(set(periods)) != len(periods):
            raise CoverageError(f"{label} contain a duplicate month")

    @staticmethod
    def _require_contiguous(periods: Sequence[YearMonth]) -> None:
        for left, right in itertools.pairwise(periods):
            if not _period_follows(left, right):
                raise CoverageError("strict coverage must be calendar-contiguous")

    def _read_verified_partition(
        self,
        request: MonthlyKlineRequest,
        ingestion: IngestionResult,
        stored,
    ) -> tuple[PriceObservation, ...]:
        manifest = stored.manifest
        if stored.validation_report.errors:
            raise ResearchDataError("verified storage returned blocking quality errors")
        if (
            manifest.symbol != request.symbol
            or manifest.market != request.market
            or manifest.interval != request.interval
            or manifest.year != request.period.year
            or manifest.month != request.period.month
        ):
            raise ResearchDataError(
                "stored partition identity does not match the request"
            )
        if manifest.source_archive_sha256 != ingestion.provenance.actual_sha256:
            raise ResearchDataError("stored lineage does not match source provenance")
        if _sha256_file(ingestion.archive_path) != manifest.source_archive_sha256:
            raise ResearchDataError("raw archive hash changed after M3 verification")
        if _sha256_file(ingestion.provenance_path) != manifest.source_provenance_sha256:
            raise ResearchDataError("provenance hash changed after M3 verification")
        try:
            with duckdb.connect(":memory:") as connection:
                connection.execute("SET TimeZone = 'UTC'")
                rows = connection.execute(
                    _READ_PARTITION_SQL,
                    [str(stored.parquet_path)],
                ).fetchall()
        except duckdb.Error as error:
            raise ResearchDataError(f"cannot read verified Parquet: {error}") from error
        if len(rows) != manifest.observation_count:
            raise ResearchDataError(
                "verified Parquet row count does not match its manifest"
            )
        observations = tuple(
            self._observation_from_row(row, request, manifest.source_archive_sha256)
            for row in rows
        )
        if not observations:
            raise ResearchDataError("verified partition contains no observations")
        if (
            observations[0].open_time != manifest.first_open_time
            or observations[-1].open_time != manifest.last_open_time
        ):
            raise ResearchDataError(
                "verified observation span does not match its manifest"
            )
        return observations

    @staticmethod
    def _observation_from_row(
        row: tuple[object, ...],
        request: MonthlyKlineRequest,
        archive_sha256: str,
    ) -> PriceObservation:
        (
            symbol,
            market,
            interval,
            open_us,
            close_us,
            close,
            source_sha,
            source_year,
            source_month,
            schema_version,
        ) = row
        if (symbol, market, interval) != (
            request.symbol,
            request.market,
            request.interval,
        ):
            raise ResearchDataError("Parquet row identity does not match the request")
        if source_sha != archive_sha256:
            raise ResearchDataError("Parquet row source hash does not match lineage")
        if (source_year, source_month) != (request.period.year, request.period.month):
            raise ResearchDataError(
                "Parquet row source period does not match the request"
            )
        if not isinstance(close, Decimal):
            raise ResearchDataError("Parquet close price was not returned as Decimal")
        open_time = _from_epoch_microseconds(open_us)
        close_time = _from_epoch_microseconds(close_us)
        if (
            open_time.minute
            or open_time.second
            or open_time.microsecond
            or open_time.year != request.period.year
            or open_time.month != request.period.month
        ):
            raise ResearchDataError(
                "verified open_time is not an exact UTC hour in its month"
            )
        if close_time != _expected_close_time(open_time):
            raise ResearchDataError(
                "verified close_time does not match the archive timestamp convention"
            )
        return PriceObservation(
            open_time=open_time,
            close_time=close_time,
            close=close,
            source_archive_sha256=archive_sha256,
            source_year=request.period.year,
            source_month=request.period.month,
            source_schema_version=str(schema_version),
        )

    @staticmethod
    def _lineage(stored) -> PartitionLineage:
        manifest = stored.manifest
        return PartitionLineage(
            year=manifest.year,
            month=manifest.month,
            source_archive_sha256=manifest.source_archive_sha256,
            source_provenance_sha256=manifest.source_provenance_sha256,
            parquet_sha256=manifest.parquet_sha256,
            validation_sha256=manifest.validation_sha256,
            observation_count=manifest.observation_count,
            warning_count=manifest.warning_count,
        )

    def _assemble(
        self,
        observations: tuple[PriceObservation, ...],
        lineage: tuple[PartitionLineage, ...],
        warnings: tuple[ValidationIssue, ...],
        configuration: ResearchConfiguration,
    ) -> ResearchDataset:
        gaps: list[CoverageGap] = []
        for previous, current in itertools.pairwise(observations):
            if current.open_time == previous.open_time:
                raise ResearchDataError(
                    "verified observations contain a duplicate timestamp"
                )
            if current.open_time < previous.open_time:
                raise ResearchDataError("verified observations are not chronological")
            if current.open_time != previous.open_time + _HOUR:
                gaps.append(
                    CoverageGap(
                        previous_open_time=previous.open_time,
                        next_open_time=current.open_time,
                    )
                )
        expected = tuple(configuration.expected_periods)
        covered = tuple(YearMonth(item.year, item.month) for item in lineage)
        contiguous = all(
            _period_follows(left, right) for left, right in itertools.pairwise(covered)
        )
        is_complete = (
            covered == expected and not gaps and contiguous and bool(observations)
        )
        if configuration.coverage_policy is CoveragePolicy.STRICT and not is_complete:
            raise CoverageError(
                "strict verified coverage contains gaps or missing months"
            )
        return ResearchDataset(
            observations=observations,
            expected_periods=expected,
            covered_periods=covered,
            gaps=tuple(gaps),
            lineage=lineage,
            warnings=warnings,
            access_mode=configuration.access_mode,
            coverage_policy=configuration.coverage_policy,
            final_evaluation_authorized=configuration.final_evaluation_authorized,
            is_complete=is_complete,
        )
