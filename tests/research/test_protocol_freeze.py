"""The candidate protocol is stable, sensitive, and not self-approved."""

from datetime import UTC, datetime

import pytest

from quantforge.research.protocol_freeze import (
    ProtocolApprovalState,
    ProtocolFreeze,
    ProtocolFreezeError,
    candidate_protocol_document,
    candidate_protocol_freeze,
    draft_protocol_freeze,
    protocol_document_id,
)


def test_the_candidate_id_is_stable_and_not_approved():
    first = candidate_protocol_freeze()
    second = candidate_protocol_freeze()

    assert first.protocol_id == second.protocol_id
    assert first.approval_state is ProtocolApprovalState.PENDING_HUMAN_APPROVAL
    assert draft_protocol_freeze().protocol_id == first.protocol_id
    assert "approval_state" not in first.document
    assert "created_at" not in first.document
    with pytest.raises(ProtocolFreezeError, match="approved"):
        ProtocolFreeze(
            document=first.document,
            protocol_id=first.protocol_id,
            approval_state=ProtocolApprovalState.APPROVED,
        )


def _mutated(path: tuple[str, ...], value: object) -> str:
    document = candidate_protocol_document()
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return protocol_document_id(document)


def test_material_research_decisions_change_the_protocol_id():
    original = candidate_protocol_freeze().protocol_id
    mutations = [
        _mutated(("momentum", "lookback_hours"), 48),
        _mutated(("mean_reversion", "lookback_hours"), 12),
        _mutated(("mean_reversion", "threshold"), "0.05"),
        _mutated(("latency_bars",), 2),
        _mutated(("initial_cash",), "20000"),
        _mutated(("quanta", "btc"), "0.00000002"),
        _mutated(("quanta", "cash"), "0.00000002"),
        _mutated(("analytics_version",), "m7-analytics-v2"),
        _mutated(("walk_forward_version",), "m8-walk-forward-v2"),
        _mutated(("protocol_candidate_version",), "m9-protocol-candidate-v2"),
        _mutated(
            ("scenarios",),
            [{"fee_rate": "0.003", "name": "baseline", "slippage_rate": "0.0005"}],
        ),
        _mutated(
            ("scenarios",),
            [
                {"fee_rate": "0", "name": "zero", "slippage_rate": "0"},
                {"fee_rate": "0.001", "name": "baseline", "slippage_rate": "0.009"},
                {
                    "fee_rate": "0.002",
                    "name": "pessimistic_2x",
                    "slippage_rate": "0.001",
                },
            ],
        ),
        _mutated(
            ("splits", "holdout"),
            ["2025-01-01T00:00:00.000000Z", "2026-02-01T00:00:00.000000Z"],
        ),
        _mutated(("valuation_convention",), "close_to_close"),
        _mutated(("return_convention",), "close_to_close"),
        _mutated(("benchmark_entry_policy",), "wait_for_a_long_signal"),
        _mutated(("terminal_mark_policy",), "liquidate_at_the_end"),
    ]

    assert all(mutated != original for mutated in mutations)
    assert len(set(mutations)) == len(mutations)


def test_approval_and_timestamps_cannot_enter_the_decision_hash():
    document = candidate_protocol_document()
    document["approval_state"] = "APPROVED"
    with pytest.raises(ProtocolFreezeError, match="approval"):
        protocol_document_id(document)
    document = candidate_protocol_document()
    document["created_at"] = datetime(2026, 10, 15, tzinfo=UTC).isoformat()
    with pytest.raises(ProtocolFreezeError, match="timestamp"):
        protocol_document_id(document)
