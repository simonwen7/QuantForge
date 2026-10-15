"""Scoped research-access checks. These are workflow controls, not a lock.

A caller who can edit this file or open a data directory can bypass it.
Nothing here is a cryptographic credential, an operating-system permission,
or a guarantee that the 2025 holdout can be evaluated only once. While
``ACCEPTANCE_ENABLED`` is false, no record authorizes empirical access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from quantforge.ingestion.models import YearMonth
from quantforge.research.models import ResearchError

ACCEPTANCE_ENABLED = False
HOLDOUT_STATEMENT = (
    "FINAL_HOLDOUT_NOT_EXECUTED — Authorization and/or verified data "
    "prerequisites remain unresolved."
)
TRAIN_NOT_EXECUTED = "TRAIN_NOT_EXECUTED"
VALIDATION_NOT_EXECUTED = "VALIDATION_NOT_EXECUTED"


class AccessError(ResearchError):
    """An authorization record cannot be interpreted."""


class ResearchScope(StrEnum):
    """One partition. These scopes are not interchangeable."""

    VERIFIED_TRAIN = "verified_train"
    VERIFIED_VALIDATION = "verified_validation"
    VERIFIED_HOLDOUT = "verified_holdout"


class RecordOrigin(StrEnum):
    """Where an authorization record claims to have come from."""

    SYNTHETIC_TEST_FIXTURE = "synthetic_test_fixture"
    EXTERNAL_HUMAN_ARTIFACT = "external_human_artifact"


@dataclass(frozen=True)
class AuthorizationRecord:
    """A caller-supplied record. Possession of this object grants nothing."""

    scope: ResearchScope
    protocol_freeze_id: str
    origin: RecordOrigin
    statement: str
    expires_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ResearchScope):
            raise AccessError("authorization scope is not a research scope")
        if not isinstance(self.origin, RecordOrigin):
            raise AccessError("authorization origin is not recognized")
        if self.expires_at_utc.tzinfo is not UTC:
            raise AccessError("authorization expiry must use datetime.UTC")


@dataclass(frozen=True)
class AccessDecision:
    """The workflow result. ``empirical_access`` stays false in this phase."""

    requested_scope: ResearchScope
    authorized: bool
    empirical_access: bool
    defect: str | None

    def __post_init__(self) -> None:
        if self.empirical_access or self.authorized:
            raise AccessError("this phase cannot authorize empirical access")


def _hex_id(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def authorization_defect(
    record: AuthorizationRecord | None,
    *,
    requested_scope: ResearchScope,
    protocol_freeze_id: str,
    periods: Sequence[YearMonth],
    now: datetime,
) -> str | None:
    """Return the first reason a record is unusable, without granting access.

    ``None`` means the record is structurally usable. It still does not
    authorize a study while acceptance is disabled.
    """
    if now.tzinfo is not UTC:
        raise AccessError("authorization checks require a UTC clock reading")
    if record is None:
        return "missing authorization record"
    if not record.statement.strip():
        return "malformed authorization record"
    if not _hex_id(record.protocol_freeze_id) or not _hex_id(protocol_freeze_id):
        return "malformed authorization record"
    if record.expires_at_utc <= now:
        return "expired authorization record"
    if record.origin is RecordOrigin.SYNTHETIC_TEST_FIXTURE:
        return "synthetic test fixture is not a human authorization"
    if record.origin is not RecordOrigin.EXTERNAL_HUMAN_ARTIFACT:
        return "inappropriate authorization record"
    if record.protocol_freeze_id != protocol_freeze_id:
        return "authorization protocol id does not match the candidate freeze"
    if record.scope is not requested_scope:
        return "authorization scope does not match the requested scope"
    for period in periods:
        if (
            period.year >= 2025
            and requested_scope is not ResearchScope.VERIFIED_HOLDOUT
        ):
            return "validation and train scopes cannot authorize 2025"
        if period.year >= 2024 and requested_scope is ResearchScope.VERIFIED_TRAIN:
            return "train scope cannot authorize 2024 or 2025"
    return None


def decide_access(
    record: AuthorizationRecord | None,
    *,
    requested_scope: ResearchScope,
    protocol_freeze_id: str,
    periods: Sequence[YearMonth] = (),
    now: datetime | None = None,
) -> AccessDecision:
    """Deny empirical access. A matching record does not open market data."""
    if ACCEPTANCE_ENABLED:
        raise AccessError("empirical acceptance must stay disabled")
    inspected_at = now if now is not None else datetime(2026, 1, 1, tzinfo=UTC)
    defect = authorization_defect(
        record,
        requested_scope=requested_scope,
        protocol_freeze_id=protocol_freeze_id,
        periods=periods,
        now=inspected_at,
    )
    if defect is None:
        defect = "human-approval acceptance is disabled"
    return AccessDecision(
        requested_scope=requested_scope,
        authorized=False,
        empirical_access=False,
        defect=defect,
    )


def periods_for_scope(scope: ResearchScope) -> tuple[int, int]:
    """Return the inclusive year bounds a scope is allowed to name."""
    if scope is ResearchScope.VERIFIED_TRAIN:
        return (2021, 2023)
    if scope is ResearchScope.VERIFIED_VALIDATION:
        return (2021, 2024)
    if scope is ResearchScope.VERIFIED_HOLDOUT:
        return (2021, 2025)
    raise AccessError(f"unsupported research scope: {scope!r}")
