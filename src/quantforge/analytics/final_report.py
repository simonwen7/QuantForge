"""Deterministic M9 readiness report. It contains no strategy performance.

JSON and Markdown are rendered from one payload. Wall-clock time is not part
of the report id. Output is written only to a directory the caller supplies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from quantforge.analytics.verified_study import (
    STUDY_PLAN_VERSION,
    BlockedStudy,
    StudyPlan,
    run_empirical_study,
)
from quantforge.research.access import (
    HOLDOUT_STATEMENT,
    TRAIN_NOT_EXECUTED,
    VALIDATION_NOT_EXECUTED,
    ResearchScope,
    decide_access,
)
from quantforge.research.experiment import experiment_id
from quantforge.research.protocol_freeze import (
    PROTOCOL_CANDIDATE_VERSION,
    ProtocolApprovalState,
    ProtocolFreeze,
)
from quantforge.research.readiness import (
    EvidenceStatus,
    FindingSeverity,
    PreflightResult,
    finding_payload,
    human_review_topics,
)

READINESS_VERSION = "m9-readiness-v1"
REPORT_STATEMENT = (
    "READINESS REPORT. This document does not contain BTCUSDT performance "
    "results. Legal acceptance, real-source verification, and partition "
    "authorization remain unresolved."
)
_FORBIDDEN_KEYS = frozenset(
    {
        "sharpe",
        "sortino",
        "total_return",
        "annualized_volatility",
        "max_drawdown",
        "profit",
        "performance",
    }
)


@dataclass(frozen=True)
class ReadinessReport:
    """One blocked readiness document and the three unexecuted partitions."""

    report_id: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if experiment_id(self.payload) != self.report_id:
            raise ValueError("readiness report id does not match its payload")
        _reject_performance_keys(self.payload)


def _reject_performance_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN_KEYS:
                raise ValueError(f"readiness report cannot contain {key}")
            _reject_performance_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_performance_keys(item)


def _gate(name: str, status: str, detail: str) -> dict[str, str]:
    return {"detail": detail, "name": name, "status": status}


def _quality_status(preflight: PreflightResult) -> str:
    if any(item.severity is FindingSeverity.HARD_FAIL for item in preflight.findings):
        return "QUALITY_FAILED"
    if preflight.synthetic_fixture_passed:
        return "SYNTHETIC_FIXTURE_NO_HARD_FAILURE"
    return "DATA_UNVERIFIED"


def readiness_payload(
    protocol: ProtocolFreeze,
    preflight: PreflightResult,
    study: BlockedStudy,
) -> dict[str, object]:
    """Build the canonical readiness document from an unexecuted study."""
    if protocol.approval_state is ProtocolApprovalState.APPROVED:
        raise ValueError("a readiness report cannot describe an approved protocol")
    counts = {status.value: 0 for status in EvidenceStatus}
    for month in preflight.months:
        counts[month.status.value] += 1
    train = decide_access(
        None,
        requested_scope=ResearchScope.VERIFIED_TRAIN,
        protocol_freeze_id=protocol.protocol_id,
    )
    validation = decide_access(
        None,
        requested_scope=ResearchScope.VERIFIED_VALIDATION,
        protocol_freeze_id=protocol.protocol_id,
    )
    holdout = decide_access(
        None,
        requested_scope=ResearchScope.VERIFIED_HOLDOUT,
        protocol_freeze_id=protocol.protocol_id,
    )
    gates = [
        _gate(
            "legal",
            "LEGAL_UNVERIFIED",
            "Binance Vision terms have not been accepted in this repository",
        ),
        _gate(
            "source",
            "SOURCE_UNVERIFIED",
            "No real archive has been verified by this workflow",
        ),
        _gate(
            "data",
            "DATA_UNVERIFIED",
            "Expected months are not acquired real observations",
        ),
        _gate(
            "quality",
            _quality_status(preflight),
            "Hard failures block a future table; warnings are retained",
        ),
        _gate(
            "protocol",
            "PROTOCOL_UNAPPROVED",
            f"Candidate {protocol.protocol_id} is {protocol.approval_state.value}",
        ),
        _gate("train", "TRAIN_UNAUTHORIZED", train.defect or TRAIN_NOT_EXECUTED),
        _gate(
            "validation",
            "VALIDATION_UNAUTHORIZED",
            validation.defect or VALIDATION_NOT_EXECUTED,
        ),
        _gate(
            "holdout",
            "HOLDOUT_UNAUTHORIZED",
            holdout.defect or HOLDOUT_STATEMENT,
        ),
        _gate(
            "ready",
            "NOT_READY",
            "The prerequisites for an authorized study are not met",
        ),
    ]
    return {
        "access_defect": study.access.defect,
        "activation_enabled": False,
        "empirical_sections": {
            "holdout": HOLDOUT_STATEMENT,
            "train": TRAIN_NOT_EXECUTED,
            "validation": VALIDATION_NOT_EXECUTED,
        },
        "empirical_table_allowed": False,
        "gates": gates,
        "human_review": [
            {"status": "HUMAN_REVIEW_REQUIRED", "topic": topic}
            for topic in human_review_topics()
        ],
        "instrument": "BTCUSDT",
        "interval": "1h",
        "limitations": [
            "Software tests do not establish a trading result.",
            "A local authorization record cannot stop a direct file read.",
            "One-time holdout use cannot be enforced against a caller who reruns code.",
            "Synthetic fixture success is not real-source verification.",
        ],
        "manifest_id": preflight.manifest_id,
        "market": "spot",
        "methodology_version": READINESS_VERSION,
        "month_status_counts": counts,
        "planned_outcomes": protocol.document["planned_outcomes"],
        "preflight_findings": [
            dict(finding_payload(item)) for item in preflight.findings
        ],
        "preflight_id": preflight.preflight_id,
        "protocol_approval_state": protocol.approval_state.value,
        "protocol_candidate_version": PROTOCOL_CANDIDATE_VERSION,
        "protocol_id": protocol.protocol_id,
        "real_source_verified": False,
        "reproduction": (
            "Call build_readiness_report on the candidate protocol and an "
            "in-memory preflight, then write_readiness_report to an explicit "
            "directory. Do not point it at data/raw or data/processed."
        ),
        "requested_scope": study.requested_scope,
        "statement": REPORT_STATEMENT,
        "study_plan_version": STUDY_PLAN_VERSION,
        "terminal_mark_policy": protocol.document["terminal_mark_policy"],
        "warnings_retained": [
            item["code"]
            for item in (
                dict(finding_payload(finding)) for finding in preflight.findings
            )
            if item["severity"] == FindingSeverity.WARNING.value
        ],
    }


def build_readiness_report(plan: StudyPlan) -> ReadinessReport:
    """Run the blocked study and hash the readiness payload."""
    study = run_empirical_study(plan)
    payload = readiness_payload(plan.protocol, plan.preflight, study)
    return ReadinessReport(report_id=experiment_id(payload), payload=payload)


def render_markdown(report: ReadinessReport) -> str:
    """Render Markdown from the same payload the JSON file uses."""
    payload = report.payload
    lines = [
        "# QuantForge M9 readiness report",
        "",
        str(payload["statement"]),
        "",
        f"Report id: `{report.report_id}`",
        f"Protocol id: `{payload['protocol_id']}`",
        f"Protocol state: `{payload['protocol_approval_state']}`",
        f"Manifest id: `{payload['manifest_id']}`",
        f"Preflight id: `{payload['preflight_id']}`",
        "",
        "## Partitions",
        "",
    ]
    sections = payload["empirical_sections"]
    assert isinstance(sections, dict)
    for name in ("train", "validation", "holdout"):
        lines.append(f"- {name}: {sections[name]}")
    lines.extend(["", "## Planned matrix", ""])
    lines.append("The outcome counts below are PLANNED. They were not executed.")
    planned = payload["planned_outcomes"]
    assert isinstance(planned, dict)
    for name, count in planned.items():
        lines.append(f"- {name}: {count} planned, not executed")
    lines.extend(["", "## Gates", ""])
    gates = payload["gates"]
    assert isinstance(gates, list)
    for gate in gates:
        assert isinstance(gate, dict)
        lines.append(f"- {gate['name']}: `{gate['status']}` — {gate['detail']}")
    lines.extend(
        [
            "",
            "## Terminal valuation",
            "",
            str(payload["terminal_mark_policy"]),
            "",
            (
                "The last candle belongs to its partition by open time. Its close "
                "is valued at the exclusive end and is not a liquidation. No "
                "following-year open is required."
            ),
            "",
            "## Reproduction",
            "",
            str(payload["reproduction"]),
            "",
        ]
    )
    return "\n".join(lines)


def write_readiness_report(directory: Path, report: ReadinessReport) -> None:
    """Write JSON and Markdown. The caller chooses the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(report.payload)
    payload["report_id"] = report.report_id
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    (directory / "readiness-report.json").write_text(encoded + "\n", encoding="utf-8")
    (directory / "readiness-report.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
