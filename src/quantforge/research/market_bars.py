"""Verified OHLCV bars for execution, loaded beside close-only research data.

``PriceObservation`` remains the close-only M4 input. A market bar keeps the
open that an execution event may use, while high, low, close, and volume stay
on the internal record until a completion event copies them into a completion
view. Caller-built bars from :func:`synthetic_market_bar` are not verified
market data.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import duckdb

from quantforge.ingestion.models import IngestionResult, MonthlyKlineRequest, YearMonth
from quantforge.research.experiment import ResearchConfiguration
from quantforge.research.models import (
    CoverageError,
    CoverageGap,
    CoveragePolicy,
    HoldoutAccessError,
    PartitionLineage,
    ResearchAccessMode,
    ResearchDataError,
    candle_available_at,
)
from quantforge.storage.duckdb_parquet import DuckDBParquetStore

_HOUR = timedelta(hours=1)
_READ_MARKET_BARS_SQL = """
SELECT
    symbol,
    market,
    interval,
    epoch_us(open_time),
    epoch_us(close_time),
    open,
    high,
    low,
    close,
    volume,
    source_archive_sha256,
    source_year,
    source_month,
    source_schema_version
FROM read_parquet(?, hive_partitioning = false)
ORDER BY open_time
"""


class DataOrigin(StrEnum):
    """Whether bars passed the M2/M3 verification boundary."""

    VERIFIED_STORAGE = "verified_storage"
    SYNTHETIC_UNVERIFIED = "synthetic_unverified"


@dataclass(frozen=True)
class MarketBar:
    """One hourly OHLCV record.

    Replay code may hold this object. Event consumers must receive
    :class:`quantforge.backtest.models.BarOpen` or
    :class:`quantforge.backtest.models.BarComplete` instead, so a bar-open
    handler cannot read the bar's later high, low, close, or volume.
    """

    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_archive_sha256: str
    source_year: int
    source_month: int
    source_schema_version: str
    origin: DataOrigin
    available_at: datetime

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is not UTC or self.close_time.tzinfo is not UTC:
            raise ResearchDataError("market bar timestamps must use datetime.UTC")
        if self.available_at != candle_available_at(self.open_time):
            raise ResearchDataError(
                "market bar availability must be one hour after open_time"
            )
        if not self.open_time < self.close_time < self.available_at:
            raise ResearchDataError("close_time must fall inside the open hour")
        for label, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ResearchDataError(f"{label} must be a finite positive Decimal")
        if not isinstance(self.volume, Decimal) or not self.volume.is_finite():
            raise ResearchDataError("volume must be a finite Decimal")
        if self.volume < 0:
            raise ResearchDataError("volume cannot be negative")
        if self.low > self.open or self.low > self.close or self.high < self.open:
            raise ResearchDataError("OHLC values violate low <= open/close <= high")
        if self.high < self.close:
            raise ResearchDataError("OHLC values violate low <= open/close <= high")


@dataclass(frozen=True)
class MarketBarDataset:
    """Verified market bars and the coverage of the request that produced them."""

    bars: tuple[MarketBar, ...]
    expected_periods: tuple[YearMonth, ...]
    covered_periods: tuple[YearMonth, ...]
    gaps: tuple[CoverageGap, ...]
    lineage: tuple[PartitionLineage, ...]
    access_mode: ResearchAccessMode
    coverage_policy: CoveragePolicy
    final_evaluation_authorized: bool
    is_complete: bool
    origin: DataOrigin

    def __post_init__(self) -> None:
        if self.origin is not DataOrigin.VERIFIED_STORAGE:
            raise ResearchDataError(
                "a market-bar dataset must come from verified storage"
            )
        if any(bar.origin is not DataOrigin.VERIFIED_STORAGE for bar in self.bars):
            raise ResearchDataError("verified dataset contains an unverified bar")


def synthetic_market_bar(
    open_time: datetime,
    open_price: Decimal | int | str,
    *,
    high: Decimal | int | str | None = None,
    low: Decimal | int | str | None = None,
    close: Decimal | int | str | None = None,
    volume: Decimal | int | str = "1",
    archive_sha: str = "a" * 64,
) -> MarketBar:
    """Build one in-memory bar for tests.

    The result is labeled synthetic. It is not verified market data and it
    does not represent a Binance archive.
    """
    if open_time.tzinfo is not UTC:
        raise ResearchDataError("synthetic bar open_time must use datetime.UTC")
    tick = (
        timedelta(milliseconds=1)
        if open_time.year < 2025
        else timedelta(microseconds=1)
    )
    open_decimal = Decimal(open_price)
    close_decimal = open_decimal if close is None else Decimal(close)
    return MarketBar(
        open_time=open_time,
        close_time=open_time + _HOUR - tick,
        open=open_decimal,
        high=max(open_decimal, close_decimal) if high is None else Decimal(high),
        low=min(open_decimal, close_decimal) if low is None else Decimal(low),
        close=close_decimal,
        volume=Decimal(volume),
        source_archive_sha256=archive_sha,
        source_year=open_time.year,
        source_month=open_time.month,
        source_schema_version="synthetic-unverified",
        origin=DataOrigin.SYNTHETIC_UNVERIFIED,
        available_at=candle_available_at(open_time),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ResearchDataError(
            f"cannot hash market-bar source {path}: {error}"
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


class MarketBarLoader:
    """Load OHLCV bars through the same M3 publication path as research closes.

    The loader does not accept a Parquet path. Development mode rejects a 2025
    month before storage runs. Unauthorized final evaluation is rejected before
    storage runs.
    """

    def __init__(self, store: DuckDBParquetStore) -> None:
        self._store = store

    def load(
        self,
        sources: Sequence[tuple[MonthlyKlineRequest, IngestionResult]],
        configuration: ResearchConfiguration,
    ) -> MarketBarDataset:
        """Revalidate every month, then return verified bars and lineage."""
        self._enforce_access(sources, configuration)
        self._enforce_requested_shape(sources, configuration)
        bars: list[MarketBar] = []
        lineage: list[PartitionLineage] = []
        for request, ingestion in sources:
            stored = self._store.process_month(request, ingestion)
            bars.extend(self._read_verified_partition(request, ingestion, stored))
            lineage.append(self._lineage(stored))
        return self._assemble(tuple(bars), tuple(lineage), configuration)

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
            for left, right in itertools.pairwise(expected):
                if not _period_follows(left, right):
                    raise CoverageError("strict coverage must be calendar-contiguous")
        elif configuration.coverage_policy is not CoveragePolicy.EXPLICIT_PARTIAL:
            raise CoverageError(
                f"unsupported coverage policy: {configuration.coverage_policy!r}"
            )

    @staticmethod
    def _require_chronological_unique(periods: Sequence[YearMonth], label: str) -> None:
        if tuple(sorted(periods)) != tuple(periods):
            raise CoverageError(f"{label} must be strictly chronological")
        if len(set(periods)) != len(periods):
            raise CoverageError(f"{label} contain a duplicate month")

    def _read_verified_partition(
        self,
        request: MonthlyKlineRequest,
        ingestion: IngestionResult,
        stored,
    ) -> tuple[MarketBar, ...]:
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
                    _READ_MARKET_BARS_SQL,
                    [str(stored.parquet_path)],
                ).fetchall()
        except duckdb.Error as error:
            raise ResearchDataError(f"cannot read verified Parquet: {error}") from error
        if len(rows) != manifest.observation_count:
            raise ResearchDataError(
                "verified Parquet row count does not match its manifest"
            )
        bars = tuple(
            self._bar_from_row(row, request, manifest.source_archive_sha256)
            for row in rows
        )
        if not bars:
            raise ResearchDataError("verified partition contains no market bars")
        if (
            bars[0].open_time != manifest.first_open_time
            or bars[-1].open_time != manifest.last_open_time
        ):
            raise ResearchDataError("verified bar span does not match its manifest")
        return bars

    @staticmethod
    def _bar_from_row(
        row: tuple[object, ...],
        request: MonthlyKlineRequest,
        archive_sha256: str,
    ) -> MarketBar:
        (
            symbol,
            market,
            interval,
            open_us,
            close_us,
            open_price,
            high,
            low,
            close,
            volume,
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
        prices = {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        if any(not isinstance(value, Decimal) for value in prices.values()):
            raise ResearchDataError("Parquet OHLCV value was not returned as Decimal")
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
        return MarketBar(
            open_time=open_time,
            close_time=close_time,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            source_archive_sha256=archive_sha256,
            source_year=request.period.year,
            source_month=request.period.month,
            source_schema_version=str(schema_version),
            origin=DataOrigin.VERIFIED_STORAGE,
            available_at=candle_available_at(open_time),
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
        bars: tuple[MarketBar, ...],
        lineage: tuple[PartitionLineage, ...],
        configuration: ResearchConfiguration,
    ) -> MarketBarDataset:
        gaps: list[CoverageGap] = []
        for previous, current in itertools.pairwise(bars):
            if current.open_time == previous.open_time:
                raise ResearchDataError("verified bars contain a duplicate timestamp")
            if current.open_time < previous.open_time:
                raise ResearchDataError("verified bars are not chronological")
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
        is_complete = covered == expected and not gaps and contiguous and bool(bars)
        if configuration.coverage_policy is CoveragePolicy.STRICT and not is_complete:
            raise CoverageError(
                "strict verified coverage contains gaps or missing months"
            )
        return MarketBarDataset(
            bars=bars,
            expected_periods=expected,
            covered_periods=covered,
            gaps=tuple(gaps),
            lineage=lineage,
            access_mode=configuration.access_mode,
            coverage_policy=configuration.coverage_policy,
            final_evaluation_authorized=configuration.final_evaluation_authorized,
            is_complete=is_complete,
            origin=DataOrigin.VERIFIED_STORAGE,
        )
