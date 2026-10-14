"""Shared builders for synthetic research observations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantforge.ingestion import YearMonth
from quantforge.research import (
    CoveragePolicy,
    FeatureSpecification,
    PriceObservation,
    ResearchAccessMode,
    ResearchConfiguration,
    ReturnKind,
)

HOUR = timedelta(hours=1)


def feature_specification(**overrides) -> FeatureSpecification:
    values = {
        "return_kind": ReturnKind.LOG,
        "rolling_window": 3,
        "autocorrelation_lags": (1,),
    }
    values.update(overrides)
    return FeatureSpecification(**values)


def research_configuration(
    periods: tuple[YearMonth, ...],
    *,
    access_mode: ResearchAccessMode = ResearchAccessMode.DEVELOPMENT,
    coverage_policy: CoveragePolicy = CoveragePolicy.STRICT,
    authorized: bool = False,
) -> ResearchConfiguration:
    return ResearchConfiguration(
        access_mode=access_mode,
        coverage_policy=coverage_policy,
        expected_periods=periods,
        features=feature_specification(),
        final_evaluation_authorized=authorized,
    )


def price_observation(
    open_time: datetime,
    close: Decimal | int | str,
    *,
    archive_sha: str = "a" * 64,
) -> PriceObservation:
    tick = (
        timedelta(milliseconds=1)
        if open_time.year < 2025
        else timedelta(microseconds=1)
    )
    return PriceObservation(
        open_time=open_time,
        close_time=open_time + HOUR - tick,
        close=Decimal(close),
        source_archive_sha256=archive_sha,
        source_year=open_time.year,
        source_month=open_time.month,
        source_schema_version="binance-spot-kline-12-v1",
    )


def hourly_prices(
    start: datetime,
    closes: tuple[Decimal | int | str, ...],
) -> tuple[PriceObservation, ...]:
    if start.tzinfo is not UTC:
        raise ValueError("start must be UTC")
    return tuple(
        price_observation(start + offset * HOUR, close)
        for offset, close in enumerate(closes)
    )
