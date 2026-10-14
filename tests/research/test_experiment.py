"""Deterministic experiment identity tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantforge.ingestion import YearMonth
from quantforge.quality.models import IssueCode, Severity, ValidationIssue
from quantforge.research import (
    CoveragePolicy,
    HoldoutAccessError,
    PartitionLineage,
    ResearchAccessMode,
    ResearchDataset,
    build_experiment_manifest,
    canonical_json,
    serialize_experiment,
)
from tests.research.support import hourly_prices, research_configuration


def _dataset(period: YearMonth, *, warning: bool = False) -> ResearchDataset:
    observations = hourly_prices(
        datetime(period.year, period.month, 1, tzinfo=UTC),
        (Decimal(100), Decimal(110), Decimal(121)),
    )
    warnings = ()
    if warning:
        warnings = (
            ValidationIssue(
                severity=Severity.WARNING,
                code=IssueCode.ZERO_VOLUME,
                message="synthetic warning",
            ),
        )
    lineage = (
        PartitionLineage(
            year=period.year,
            month=period.month,
            source_archive_sha256="a" * 64,
            source_provenance_sha256="b" * 64,
            parquet_sha256="c" * 64,
            validation_sha256="d" * 64,
            observation_count=len(observations),
            warning_count=len(warnings),
        ),
    )
    return ResearchDataset(
        observations=observations,
        expected_periods=(period,),
        covered_periods=(period,),
        gaps=(),
        lineage=lineage,
        warnings=warnings,
        access_mode=ResearchAccessMode.DEVELOPMENT,
        coverage_policy=CoveragePolicy.STRICT,
        final_evaluation_authorized=False,
        is_complete=True,
    )


def test_experiment_identifier_excludes_execution_time_and_is_deterministic():
    period = YearMonth(2024, 1)
    configuration = research_configuration((period,))
    dataset = _dataset(period)
    first_time = datetime(2026, 3, 1, tzinfo=UTC)
    second_time = datetime(2026, 3, 2, tzinfo=UTC)

    first = build_experiment_manifest(
        configuration,
        dataset,
        clock=lambda: first_time,
    )
    second = build_experiment_manifest(
        configuration,
        dataset,
        clock=lambda: second_time,
    )
    serialized = serialize_experiment(first)

    assert first.experiment_id == second.experiment_id
    assert first.identity == second.identity
    assert first.created_at_utc == first_time
    assert second.created_at_utc == second_time
    identity_text = canonical_json(first.identity)
    required_fields = {
        "access_mode",
        "actual_periods",
        "annualization_note",
        "annualization_periods",
        "autocorrelation_definition",
        "autocorrelation_lags",
        "availability_rule",
        "coverage_policy",
        "decimal_precision",
        "duckdb_version",
        "expected_periods",
        "final_evaluation_authorized",
        "float_boundary",
        "gaps",
        "is_complete",
        "label_availability_rule",
        "label_horizon_hours",
        "lineage",
        "protocol_version",
        "python_version",
        "quantile_convention",
        "quantforge_version",
        "return_formula",
        "return_kind",
        "rolling_window",
        "splits",
        "standard_deviation",
        "statistic_alignment_rule",
        "warning_codes",
        "warmup_observations",
    }

    assert "created_at_utc" not in identity_text
    assert required_fields <= set(first.identity)
    assert first.identity["splits"]["train"][0] == "2021-01-01T00:00:00.000000Z"
    assert first.identity["splits"]["validation"][1] == "2025-01-01T00:00:00.000000Z"
    assert first.identity["splits"]["holdout"][1] == "2026-01-01T00:00:00.000000Z"
    assert first.identity["decimal_precision"] == 50
    assert first.identity["lineage"][0]["source_archive_sha256"] == "a" * 64
    assert first.experiment_id in serialized
    assert "available_at = open_time + 1 hour" in serialized
    assert "strictly before the exclusive split end" in serialized
    assert "binary64" in serialized


def test_source_lineage_change_changes_experiment_identifier():
    period = YearMonth(2024, 1)
    configuration = research_configuration((period,))
    original = _dataset(period)
    changed_lineage = original.lineage[0]
    changed = ResearchDataset(
        observations=original.observations,
        expected_periods=original.expected_periods,
        covered_periods=original.covered_periods,
        gaps=original.gaps,
        lineage=(
            PartitionLineage(
                year=changed_lineage.year,
                month=changed_lineage.month,
                source_archive_sha256="e" * 64,
                source_provenance_sha256=changed_lineage.source_provenance_sha256,
                parquet_sha256=changed_lineage.parquet_sha256,
                validation_sha256=changed_lineage.validation_sha256,
                observation_count=changed_lineage.observation_count,
                warning_count=changed_lineage.warning_count,
            ),
        ),
        warnings=original.warnings,
        access_mode=original.access_mode,
        coverage_policy=original.coverage_policy,
        final_evaluation_authorized=original.final_evaluation_authorized,
        is_complete=original.is_complete,
    )

    assert (
        build_experiment_manifest(
            configuration,
            original,
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
        ).experiment_id
        != build_experiment_manifest(
            configuration,
            changed,
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
        ).experiment_id
    )


def test_development_manifest_rejects_recorded_holdout_coverage():
    period = YearMonth(2025, 1)
    dataset = _dataset(period)
    dataset = ResearchDataset(
        observations=dataset.observations,
        expected_periods=(period,),
        covered_periods=(period,),
        gaps=(),
        lineage=dataset.lineage,
        warnings=(),
        access_mode=ResearchAccessMode.DEVELOPMENT,
        coverage_policy=CoveragePolicy.STRICT,
        final_evaluation_authorized=False,
        is_complete=True,
    )

    with pytest.raises(HoldoutAccessError):
        build_experiment_manifest(
            research_configuration((period,)),
            dataset,
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
        )


def test_manifest_rejects_development_authorization_and_unauthorized_final_mode():
    period = YearMonth(2024, 1)
    authorized_development = ResearchDataset(
        observations=_dataset(period).observations,
        expected_periods=(period,),
        covered_periods=(period,),
        gaps=(),
        lineage=_dataset(period).lineage,
        warnings=(),
        access_mode=ResearchAccessMode.DEVELOPMENT,
        coverage_policy=CoveragePolicy.STRICT,
        final_evaluation_authorized=True,
        is_complete=True,
    )
    unauthorized_final = ResearchDataset(
        observations=authorized_development.observations,
        expected_periods=(period,),
        covered_periods=(period,),
        gaps=(),
        lineage=authorized_development.lineage,
        warnings=(),
        access_mode=ResearchAccessMode.FINAL_EVALUATION,
        coverage_policy=CoveragePolicy.STRICT,
        final_evaluation_authorized=False,
        is_complete=True,
    )

    with pytest.raises(HoldoutAccessError, match="authorize"):
        build_experiment_manifest(
            research_configuration((period,), authorized=True),
            authorized_development,
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
        )
    with pytest.raises(HoldoutAccessError, match="explicit authorization"):
        build_experiment_manifest(
            research_configuration(
                (period,),
                access_mode=ResearchAccessMode.FINAL_EVALUATION,
            ),
            unauthorized_final,
            clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
        )
