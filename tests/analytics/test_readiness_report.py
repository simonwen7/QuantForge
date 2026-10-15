"""The default M9 report is a blocked readiness document."""

import inspect
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quantforge.analytics.final_report import (
    build_readiness_report,
    render_markdown,
    write_readiness_report,
)
from quantforge.analytics.verified_study import (
    ACTIVATION_ENABLED,
    StudyPlan,
    run_empirical_study,
)
from quantforge.ingestion.models import YearMonth
from quantforge.research.access import (
    HOLDOUT_STATEMENT,
    TRAIN_NOT_EXECUTED,
    VALIDATION_NOT_EXECUTED,
    AuthorizationRecord,
    RecordOrigin,
    ResearchScope,
)
from quantforge.research.experiment import experiment_id
from quantforge.research.protocol_freeze import (
    ProtocolFreeze,
    candidate_protocol_document,
    candidate_protocol_freeze,
    draft_protocol_freeze,
    protocol_document_id,
)
from quantforge.research.readiness import assess_evidence, expected_archives
from tests.research.test_readiness import _evidence

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _plan(protocol=None) -> StudyPlan:
    return StudyPlan(
        protocol=protocol or candidate_protocol_freeze(),
        requested_scope=ResearchScope.VERIFIED_HOLDOUT,
        authorization=None,
        preflight=assess_evidence(),
        now=NOW,
    )


def test_the_default_report_has_no_performance_and_is_repeatable(tmp_path: Path):
    report = build_readiness_report(_plan())
    write_readiness_report(tmp_path, report)
    write_readiness_report(tmp_path / "repeat", report)
    text = (tmp_path / "readiness-report.md").read_text(encoding="utf-8")
    blob = (tmp_path / "readiness-report.json").read_text(encoding="utf-8")
    statuses = {gate["status"] for gate in report.payload["gates"]}

    assert blob == (tmp_path / "repeat" / "readiness-report.json").read_text(
        encoding="utf-8"
    )
    assert text == (tmp_path / "repeat" / "readiness-report.md").read_text(
        encoding="utf-8"
    )
    assert text == render_markdown(report)
    assert TRAIN_NOT_EXECUTED in text
    assert VALIDATION_NOT_EXECUTED in text
    assert HOLDOUT_STATEMENT in text
    assert "LEGAL_UNVERIFIED" in statuses
    assert "SOURCE_UNVERIFIED" in statuses
    assert "DATA_UNVERIFIED" in statuses
    assert "PROTOCOL_UNAPPROVED" in statuses
    assert "TRAIN_UNAUTHORIZED" in statuses
    assert "VALIDATION_UNAUTHORIZED" in statuses
    assert "HOLDOUT_UNAUTHORIZED" in statuses
    assert "READY_FOR_AUTHORIZED_RESEARCH" not in statuses
    assert report.payload["empirical_table_allowed"] is False
    assert report.payload["real_source_verified"] is False
    assert "sharpe" not in blob.lower()
    assert "total_return" not in blob
    assert "not executed" in text
    assert "PLANNED" in text
    assert "created_at" not in report.payload
    forbidden = {
        "sharpe",
        "sortino",
        "total_return",
        "annualized_volatility",
        "max_drawdown",
        "profit",
        "performance",
        "pnl",
        "realized_pnl",
    }

    def walk(value):
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(report.payload)
    assert all(
        isinstance(count, int) for count in report.payload["planned_outcomes"].values()
    )
    assert any(
        item["status"] == "HUMAN_REVIEW_REQUIRED"
        for item in report.payload["human_review"]
    )


def test_a_material_protocol_change_and_a_reader_cannot_execute(tmp_path: Path):
    original = build_readiness_report(_plan())
    document = candidate_protocol_document()
    document["terminal_mark_policy"] = "liquidate_at_the_end"
    changed = ProtocolFreeze(
        document=document,
        protocol_id=protocol_document_id(document),
    )
    revised = build_readiness_report(_plan(changed))

    def reader():
        raise AssertionError("reader was called")

    with pytest.raises(Exception, match="disabled"):
        run_empirical_study(
            _plan(), source_path=Path("/tmp/quantforge-not-opened"), reader=reader
        )

    assert revised.report_id != original.report_id
    assert ACTIVATION_ENABLED is False
    assert "run_verified" not in inspect.getsource(run_empirical_study)
    assert "MarketBarLoader" not in inspect.getsource(run_empirical_study)


def test_a_synthetic_authorization_record_does_not_open_a_path():
    protocol = candidate_protocol_freeze()
    record = AuthorizationRecord(
        scope=ResearchScope.VERIFIED_HOLDOUT,
        protocol_freeze_id=protocol.protocol_id,
        origin=RecordOrigin.SYNTHETIC_TEST_FIXTURE,
        statement="fixture",
        expires_at_utc=NOW + timedelta(days=1),
    )
    study = run_empirical_study(
        StudyPlan(
            protocol=protocol,
            requested_scope=ResearchScope.VERIFIED_HOLDOUT,
            authorization=record,
            preflight=assess_evidence(),
            now=NOW,
        )
    )

    assert study.empirical_table_allowed is False
    assert study.holdout_state == HOLDOUT_STATEMENT
    assert study.access.empirical_access is False


class _Trap:
    def __getattr__(self, name):
        raise AssertionError(f"path was touched: {name}")


def test_denials_leave_the_reader_and_path_untouched():
    called = False

    def reader():
        nonlocal called
        called = True
        raise AssertionError("reader was called")

    protocol = candidate_protocol_freeze()
    january = next(
        item for item in expected_archives() if item.period == YearMonth(2021, 1)
    )
    passed = assess_evidence((_evidence(YearMonth(2021, 1)),), expected=(january,))
    assert passed.synthetic_fixture_passed is True
    grants = [
        None,
        AuthorizationRecord(
            scope=ResearchScope.VERIFIED_TRAIN,
            protocol_freeze_id=protocol.protocol_id,
            origin=RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
            statement="wrong scope",
            expires_at_utc=NOW + timedelta(days=1),
        ),
        AuthorizationRecord(
            scope=ResearchScope.VERIFIED_HOLDOUT,
            protocol_freeze_id="b" * 64,
            origin=RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
            statement="wrong protocol",
            expires_at_utc=NOW + timedelta(days=1),
        ),
        AuthorizationRecord(
            scope=ResearchScope.VERIFIED_HOLDOUT,
            protocol_freeze_id=protocol.protocol_id,
            origin=RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
            statement="   ",
            expires_at_utc=NOW + timedelta(days=1),
        ),
        AuthorizationRecord(
            scope=ResearchScope.VERIFIED_HOLDOUT,
            protocol_freeze_id=protocol.protocol_id,
            origin=RecordOrigin.EXTERNAL_HUMAN_ARTIFACT,
            statement="structurally complete and still unaccepted",
            expires_at_utc=NOW + timedelta(days=1),
        ),
    ]
    for grant in grants:
        study = run_empirical_study(
            StudyPlan(
                protocol=protocol,
                requested_scope=ResearchScope.VERIFIED_HOLDOUT,
                authorization=grant,
                preflight=passed,
                now=NOW,
            )
        )
        assert study.holdout_state == HOLDOUT_STATEMENT
        assert study.train_state == TRAIN_NOT_EXECUTED
        assert study.validation_state == VALIDATION_NOT_EXECUTED
        assert study.empirical_table_allowed is False
        with pytest.raises(Exception, match="disabled"):
            run_empirical_study(
                StudyPlan(
                    protocol=protocol,
                    requested_scope=ResearchScope.VERIFIED_HOLDOUT,
                    authorization=grant,
                    preflight=passed,
                    now=NOW,
                ),
                source_path=_Trap(),
                reader=reader,
            )
    assert called is False


def test_approval_scope_and_preflight_change_the_report_id_only():
    pending = build_readiness_report(_plan())
    draft = build_readiness_report(_plan(draft_protocol_freeze()))
    train = build_readiness_report(
        StudyPlan(
            protocol=candidate_protocol_freeze(),
            requested_scope=ResearchScope.VERIFIED_TRAIN,
            authorization=None,
            preflight=assess_evidence(),
            now=NOW,
        )
    )
    synthetic = build_readiness_report(
        StudyPlan(
            protocol=candidate_protocol_freeze(),
            requested_scope=ResearchScope.VERIFIED_HOLDOUT,
            authorization=AuthorizationRecord(
                scope=ResearchScope.VERIFIED_HOLDOUT,
                protocol_freeze_id=candidate_protocol_freeze().protocol_id,
                origin=RecordOrigin.SYNTHETIC_TEST_FIXTURE,
                statement="fixture",
                expires_at_utc=NOW + timedelta(days=1),
            ),
            preflight=assess_evidence(),
            now=NOW,
        )
    )
    revised = dict(pending.payload)
    revised["methodology_version"] = "m9-readiness-v2"

    assert draft.payload["protocol_id"] == pending.payload["protocol_id"]
    assert draft.report_id != pending.report_id
    assert train.report_id != pending.report_id
    assert synthetic.report_id != pending.report_id
    assert experiment_id(revised) != pending.report_id
    assert "READY_FOR_AUTHORIZED_RESEARCH" not in {
        gate["status"] for gate in synthetic.payload["gates"]
    }


def test_m9_modules_import_without_recreating_the_package_cycle():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import quantforge.research, quantforge.analytics; "
                "import quantforge.research.protocol_freeze; "
                "import quantforge.research.access; "
                "import quantforge.research.readiness; "
                "import quantforge.research.boundary; "
                "import quantforge.analytics.verified_study; "
                "import quantforge.analytics.final_report; "
                "from quantforge.research import experiment_id; "
                "from quantforge.analytics import run_synthetic_study"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
