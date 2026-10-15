"""Scoped records are denied, and a clean record still cannot open data."""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from quantforge.ingestion.models import YearMonth
from quantforge.research.access import (
    ACCEPTANCE_ENABLED,
    AccessError,
    AuthorizationRecord,
    RecordOrigin,
    ResearchScope,
    authorization_defect,
    decide_access,
)
from quantforge.research.protocol_freeze import candidate_protocol_freeze

NOW = datetime(2026, 6, 1, tzinfo=UTC)
PROTOCOL = "a" * 64


def _record(**overrides) -> AuthorizationRecord:
    payload = {
        "scope": ResearchScope.VERIFIED_VALIDATION,
        "protocol_freeze_id": PROTOCOL,
        "origin": RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
        "statement": "human approval is recorded outside this process",
        "expires_at_utc": NOW + timedelta(days=1),
    }
    payload.update(overrides)
    return AuthorizationRecord(**payload)


def _defect(record, scope, periods=()):
    return authorization_defect(
        record,
        requested_scope=scope,
        protocol_freeze_id=PROTOCOL,
        periods=periods,
        now=NOW,
    )


def test_missing_malformed_and_expired_records_are_denied():
    assert _defect(None, ResearchScope.VERIFIED_TRAIN) == "missing authorization record"
    with pytest.raises(AccessError, match="UTC"):
        AuthorizationRecord(
            scope=ResearchScope.VERIFIED_TRAIN,
            protocol_freeze_id=PROTOCOL,
            origin=RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
            statement="stated",
            expires_at_utc=datetime(2026, 6, 1),
        )
    assert (
        _defect(
            _record(statement="  "),
            ResearchScope.VERIFIED_VALIDATION,
        )
        == "malformed authorization record"
    )
    assert (
        _defect(
            _record(expires_at_utc=NOW - timedelta(seconds=1)),
            ResearchScope.VERIFIED_VALIDATION,
        )
        == "expired authorization record"
    )
    assert (
        _defect(
            _record(origin=RecordOrigin.SYNTHETIC_TEST_FIXTURE),
            ResearchScope.VERIFIED_VALIDATION,
        )
        == "synthetic test fixture is not a human authorization"
    )


def test_scope_and_protocol_mismatches_do_not_unlock_another_partition():
    assert (
        _defect(_record(protocol_freeze_id="b" * 64), ResearchScope.VERIFIED_VALIDATION)
        == "authorization protocol id does not match the candidate freeze"
    )
    assert (
        _defect(_record(), ResearchScope.VERIFIED_HOLDOUT)
        == "authorization scope does not match the requested scope"
    )
    assert (
        _defect(
            _record(scope=ResearchScope.VERIFIED_TRAIN),
            ResearchScope.VERIFIED_TRAIN,
            (YearMonth(2024, 1),),
        )
        == "train scope cannot authorize 2024 or 2025"
    )
    assert (
        _defect(
            _record(),
            ResearchScope.VERIFIED_VALIDATION,
            (YearMonth(2025, 1),),
        )
        == "validation and train scopes cannot authorize 2025"
    )


def test_a_structurally_usable_record_still_cannot_authorize_access():
    freeze = candidate_protocol_freeze()
    record = _record(
        scope=ResearchScope.VERIFIED_HOLDOUT,
        protocol_freeze_id=freeze.protocol_id,
    )
    assert (
        authorization_defect(
            record,
            requested_scope=ResearchScope.VERIFIED_HOLDOUT,
            protocol_freeze_id=freeze.protocol_id,
            periods=(YearMonth(2025, 12),),
            now=NOW,
        )
        is None
    )
    decision = decide_access(
        record,
        requested_scope=ResearchScope.VERIFIED_HOLDOUT,
        protocol_freeze_id=freeze.protocol_id,
        periods=(YearMonth(2025, 12),),
        now=NOW,
    )

    assert ACCEPTANCE_ENABLED is False
    assert decision.authorized is False
    assert decision.empirical_access is False
    assert decision.defect == "human-approval acceptance is disabled"


def test_expiry_is_half_open_and_must_be_utc():
    class OtherZeroOffset(tzinfo):
        def utcoffset(self, dt):
            return timedelta(0)

        def tzname(self, dt):
            return "UTC"

        def dst(self, dt):
            return timedelta(0)

    with pytest.raises(AccessError, match="UTC"):
        _record(expires_at_utc=datetime(2026, 6, 1, tzinfo=OtherZeroOffset()))
    assert (
        _defect(_record(expires_at_utc=NOW), ResearchScope.VERIFIED_VALIDATION)
        == "expired authorization record"
    )
    live = _record(expires_at_utc=NOW + timedelta(microseconds=1))
    assert _defect(live, ResearchScope.VERIFIED_VALIDATION) is None
    decision = decide_access(
        live,
        requested_scope=ResearchScope.VERIFIED_VALIDATION,
        protocol_freeze_id=PROTOCOL,
        now=NOW,
    )
    assert decision.authorized is False
    assert decision.empirical_access is False
