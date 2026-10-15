"""Future empirical-study interface. Real-data execution is disabled.

This module does not call the market-bar loader or ``BacktestEngine``.
Legacy entry points such as ``run_verified`` and the caller-set
``final_evaluation_authorized`` flag remain reachable from other code. They
are outside this workflow and are not secured by it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quantforge.ingestion.models import YearMonth
from quantforge.research.access import (
    HOLDOUT_STATEMENT,
    TRAIN_NOT_EXECUTED,
    VALIDATION_NOT_EXECUTED,
    AccessDecision,
    AuthorizationRecord,
    ResearchScope,
    decide_access,
)
from quantforge.research.models import ResearchError
from quantforge.research.protocol_freeze import ProtocolFreeze
from quantforge.research.readiness import PreflightResult

STUDY_PLAN_VERSION = "m9-verified-study-v1"
ACTIVATION_ENABLED = False


class EmpiricalAccessError(ResearchError):
    """An empirical study was asked to read or execute market data."""


@dataclass(frozen=True)
class StudyPlan:
    """Inputs for a future study. Paths and readers are not accepted here."""

    protocol: ProtocolFreeze
    requested_scope: ResearchScope
    authorization: AuthorizationRecord | None
    preflight: PreflightResult
    periods: tuple[YearMonth, ...] = ()
    now: datetime | None = None


@dataclass(frozen=True)
class BlockedStudy:
    """A study that did not run. It contains no performance numbers."""

    study_plan_version: str
    protocol_id: str
    requested_scope: str
    train_state: str
    validation_state: str
    holdout_state: str
    access: AccessDecision
    preflight_id: str
    empirical_table_allowed: bool
    activation_enabled: bool

    def __post_init__(self) -> None:
        if self.activation_enabled or self.empirical_table_allowed:
            raise EmpiricalAccessError("a blocked study cannot enable execution")
        if self.holdout_state != HOLDOUT_STATEMENT:
            raise EmpiricalAccessError("holdout must stay explicitly unexecuted")
        if self.train_state != TRAIN_NOT_EXECUTED:
            raise EmpiricalAccessError("train must stay explicitly unexecuted")
        if self.validation_state != VALIDATION_NOT_EXECUTED:
            raise EmpiricalAccessError("validation must stay explicitly unexecuted")
        if self.study_plan_version != STUDY_PLAN_VERSION:
            raise EmpiricalAccessError("study plan version is not the M9 contract")


def run_empirical_study(
    plan: StudyPlan,
    *,
    source_path: Path | None = None,
    reader: Callable[..., Any] | None = None,
    periods: Sequence[YearMonth] | None = None,
) -> BlockedStudy:
    """Validate the plan and return a blocked result.

    ``source_path`` and ``reader`` are rejected before either is used. This
    function does not open archives, query storage, or compute a return.
    """
    if ACTIVATION_ENABLED:
        raise EmpiricalAccessError("empirical activation must stay disabled")
    if source_path is not None or reader is not None:
        raise EmpiricalAccessError(
            "real data paths and readers are disabled in this workflow"
        )
    requested_periods = tuple(plan.periods if periods is None else periods)
    decision = decide_access(
        plan.authorization,
        requested_scope=plan.requested_scope,
        protocol_freeze_id=plan.protocol.protocol_id,
        periods=requested_periods,
        now=plan.now,
    )
    if plan.preflight.empirical_table_allowed or plan.preflight.real_source_verified:
        raise EmpiricalAccessError("preflight cannot clear an empirical study")
    return BlockedStudy(
        study_plan_version=STUDY_PLAN_VERSION,
        protocol_id=plan.protocol.protocol_id,
        requested_scope=plan.requested_scope.value,
        train_state=TRAIN_NOT_EXECUTED,
        validation_state=VALIDATION_NOT_EXECUTED,
        holdout_state=HOLDOUT_STATEMENT,
        access=decision,
        preflight_id=plan.preflight.preflight_id,
        empirical_table_allowed=False,
        activation_enabled=False,
    )
