"""Deterministic research configuration and experiment identifiers."""

from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from types import MappingProxyType
from typing import Any

import duckdb

from quantforge.ingestion.models import YearMonth
from quantforge.research.models import (
    ANNUALIZATION_NOTE,
    AUTOCORRELATION_DEFINITION,
    AVAILABILITY_RULE,
    FLOAT_BOUNDARY_NOTE,
    LABEL_AVAILABILITY_RULE,
    QUANTILE_CONVENTION,
    RESEARCH_PROTOCOL_VERSION,
    SAMPLE_STANDARD_DEVIATION,
    STATISTIC_ALIGNMENT_RULE,
    CoveragePolicy,
    FeatureSpecification,
    HoldoutAccessError,
    PartitionLineage,
    ResearchAccessMode,
    ResearchDataError,
    ResearchDataset,
    ResearchError,
)
from quantforge.research.protocol import (
    HOLDOUT_END,
    HOLDOUT_START,
    TRAIN_START,
    VALIDATION_START,
)

_RETURN_FORMULAS = {
    "simple": "P_t / P_(t-1) - 1",
    "log": "ln(P_t) - ln(P_(t-1))",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ResearchError("experiment timestamps must be timezone-aware")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _period_key(period: YearMonth) -> str:
    return f"{period.year:04d}-{period.month:02d}"


@dataclass(frozen=True)
class SoftwareVersions:
    """Runtime versions bound into an experiment identifier."""

    python: str
    duckdb: str
    quantforge: str


@dataclass(frozen=True)
class ResearchConfiguration:
    """Immutable inputs that define one research dataset request."""

    access_mode: ResearchAccessMode
    coverage_policy: CoveragePolicy
    expected_periods: tuple[YearMonth, ...]
    features: FeatureSpecification
    final_evaluation_authorized: bool = False
    protocol_version: str = RESEARCH_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != RESEARCH_PROTOCOL_VERSION:
            raise ResearchError("unsupported research protocol version")


def current_software_versions() -> SoftwareVersions:
    """Read installed versions without inventing missing metadata."""
    try:
        quantforge_version = version("quantforge")
    except PackageNotFoundError as error:
        raise ResearchError("installed quantforge version is unavailable") from error
    return SoftwareVersions(
        python=platform.python_version(),
        duckdb=duckdb.__version__,
        quantforge=quantforge_version,
    )


def _lineage_payload(lineage: PartitionLineage) -> dict[str, object]:
    return {
        "month": lineage.month,
        "observation_count": lineage.observation_count,
        "parquet_sha256": lineage.parquet_sha256,
        "source_archive_sha256": lineage.source_archive_sha256,
        "source_provenance_sha256": lineage.source_provenance_sha256,
        "validation_sha256": lineage.validation_sha256,
        "warning_count": lineage.warning_count,
        "year": lineage.year,
    }


def identity_payload(
    configuration: ResearchConfiguration,
    dataset: ResearchDataset,
    versions: SoftwareVersions,
) -> dict[str, object]:
    """Build the canonical identity document, excluding execution time."""
    if configuration.protocol_version != RESEARCH_PROTOCOL_VERSION:
        raise ResearchError("experiment protocol version is not supported")
    if dataset.access_mode is not configuration.access_mode:
        raise ResearchDataError("dataset access mode does not match the experiment")
    if dataset.coverage_policy is not configuration.coverage_policy:
        raise ResearchDataError("dataset coverage policy does not match the experiment")
    if dataset.expected_periods != configuration.expected_periods:
        raise ResearchDataError(
            "dataset expected coverage does not match the experiment"
        )
    if dataset.final_evaluation_authorized != configuration.final_evaluation_authorized:
        raise ResearchDataError("dataset authorization does not match the experiment")
    covered_from_lineage = tuple(
        YearMonth(item.year, item.month) for item in dataset.lineage
    )
    if covered_from_lineage != dataset.covered_periods:
        raise ResearchDataError("dataset coverage does not match partition lineage")
    if sum(item.observation_count for item in dataset.lineage) != len(
        dataset.observations
    ):
        raise ResearchDataError("lineage counts do not match loaded observations")
    if sum(item.warning_count for item in dataset.lineage) != len(dataset.warnings):
        raise ResearchDataError("lineage warning counts do not match dataset warnings")
    _reject_unauthorized_holdout(configuration, dataset)
    standard_deviation = (
        SAMPLE_STANDARD_DEVIATION
        if configuration.features.ddof == 1
        else "population standard deviation with denominator n"
    )
    return {
        "access_mode": configuration.access_mode.value,
        "actual_periods": [_period_key(period) for period in dataset.covered_periods],
        "annualization_note": ANNUALIZATION_NOTE,
        "annualization_periods": configuration.features.periods_per_year,
        "autocorrelation_definition": AUTOCORRELATION_DEFINITION,
        "autocorrelation_lags": list(configuration.features.autocorrelation_lags),
        "availability_rule": AVAILABILITY_RULE,
        "coverage_policy": configuration.coverage_policy.value,
        "decimal_precision": configuration.features.decimal_precision,
        "duckdb_version": versions.duckdb,
        "expected_periods": [
            _period_key(period) for period in configuration.expected_periods
        ],
        "final_evaluation_authorized": configuration.final_evaluation_authorized,
        "float_boundary": FLOAT_BOUNDARY_NOTE,
        "gaps": [
            [
                _format_utc(gap.previous_open_time),
                _format_utc(gap.next_open_time),
            ]
            for gap in dataset.gaps
        ],
        "is_complete": dataset.is_complete,
        "label_availability_rule": LABEL_AVAILABILITY_RULE,
        "label_horizon_hours": configuration.features.label_horizon_hours,
        "lineage": [_lineage_payload(item) for item in dataset.lineage],
        "protocol_version": configuration.protocol_version,
        "python_version": versions.python,
        "quantile_convention": QUANTILE_CONVENTION,
        "quantforge_version": versions.quantforge,
        "return_formula": _RETURN_FORMULAS[configuration.features.return_kind.value],
        "return_kind": configuration.features.return_kind.value,
        "rolling_window": configuration.features.rolling_window,
        "splits": {
            "holdout": [_format_utc(HOLDOUT_START), _format_utc(HOLDOUT_END)],
            "train": [_format_utc(TRAIN_START), _format_utc(VALIDATION_START)],
            "validation": [_format_utc(VALIDATION_START), _format_utc(HOLDOUT_START)],
        },
        "standard_deviation": standard_deviation,
        "statistic_alignment_rule": STATISTIC_ALIGNMENT_RULE,
        "warning_codes": [issue.code.value for issue in dataset.warnings],
        "warmup_observations": configuration.features.warmup_observations,
    }


def canonical_json(payload: Mapping[str, object]) -> str:
    """Serialize identity JSON with stable ordering and separators."""
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def experiment_id(payload: Mapping[str, object]) -> str:
    """Hash canonical identity JSON."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _reject_unauthorized_holdout(
    configuration: ResearchConfiguration,
    dataset: ResearchDataset,
) -> None:
    includes_holdout = any(period.year >= 2025 for period in dataset.covered_periods)
    if configuration.access_mode is ResearchAccessMode.DEVELOPMENT:
        if configuration.final_evaluation_authorized:
            raise HoldoutAccessError(
                "development mode cannot authorize final-holdout access"
            )
        if includes_holdout:
            raise HoldoutAccessError(
                "development experiments cannot record final-holdout data"
            )
    if (
        configuration.access_mode is ResearchAccessMode.FINAL_EVALUATION
        and not configuration.final_evaluation_authorized
    ):
        raise HoldoutAccessError("final evaluation requires explicit authorization")


@dataclass(frozen=True)
class ExperimentManifest:
    """A reproducible experiment record with execution time stored separately."""

    experiment_id: str
    created_at_utc: datetime
    identity: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.created_at_utc.tzinfo is None:
            raise ResearchError("experiment creation time must be timezone-aware")
        object.__setattr__(self, "identity", MappingProxyType(dict(self.identity)))


def build_experiment_manifest(
    configuration: ResearchConfiguration,
    dataset: ResearchDataset,
    *,
    versions: SoftwareVersions | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> ExperimentManifest:
    """Create a manifest whose identifier excludes its execution timestamp."""
    payload = identity_payload(
        configuration,
        dataset,
        versions or current_software_versions(),
    )
    created_at = clock()
    if created_at.tzinfo is None:
        raise ResearchError("experiment clock must return a timezone-aware UTC time")
    return ExperimentManifest(
        experiment_id=experiment_id(payload),
        created_at_utc=created_at.astimezone(UTC),
        identity=payload,
    )


def serialize_experiment(manifest: ExperimentManifest) -> str:
    """Serialize an experiment without changing its hashed identity."""
    payload = {
        "created_at_utc": _format_utc(manifest.created_at_utc),
        "experiment_id": manifest.experiment_id,
        "identity": dict(manifest.identity),
    }
    return (
        json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
        )
        + "\n"
    )
