"""Terminal sessions reuse the engine instead of a second ledger."""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from quantforge.analytics.benchmarks import entry_fill_time
from quantforge.analytics.models import DEFAULT_SCENARIOS
from quantforge.analytics.returns import (
    equity_returns,
    performance_sample,
    sample_coverage,
)
from quantforge.analytics.risk import metric_set
from quantforge.api.app import create_app
from quantforge.api.serialize import session_payload
from quantforge.api.session import run_backtest, run_compare
from quantforge.backtest import BacktestEngine, ExecutionAssumptions
from quantforge.research.access import ACCEPTANCE_ENABLED
from quantforge.research.models import PriceObservation
from quantforge.strategies import MomentumConfig, MomentumStrategy
from quantforge.synthetic.scenarios import generate_scenario


def _observations(bars):
    return tuple(
        PriceObservation(
            open_time=bar.open_time,
            close_time=bar.close_time,
            close=bar.close,
            source_archive_sha256=bar.source_archive_sha256,
            source_year=bar.source_year,
            source_month=bar.source_month,
            source_schema_version=bar.source_schema_version,
        )
        for bar in bars
    )


def test_the_session_run_id_matches_a_direct_engine_call():
    session = run_backtest(
        strategy="momentum",
        lookback_hours=24,
        scenario="uptrend",
        seed=7,
        bar_count=72,
        cost="baseline",
    )
    bars = generate_scenario("uptrend", 7, 72)
    signals = MomentumStrategy(MomentumConfig(lookback_hours=24)).generate(
        _observations(bars)
    )
    assumptions = ExecutionAssumptions(
        fee_rate=DEFAULT_SCENARIOS[1].fee_rate,
        slippage_rate=DEFAULT_SCENARIOS[1].slippage_rate,
    )
    direct = BacktestEngine().run_synthetic(bars, signals, assumptions)
    primary = session.policies[0]
    assert primary.result.run_id == direct.run_id
    assert primary.result.fills == direct.fills
    assert primary.result.ledger == direct.ledger
    assert len(session.trace) == len(direct.events)
    sample = performance_sample(direct.equity_points, entry_fill_time(signals))
    returns = equity_returns(sample, opening_anchor=True)
    coverage = sample_coverage(
        sample,
        returns,
        evaluation_end=sample[-1].time,
    )
    direct_metrics = metric_set(
        sample,
        returns,
        hourly_grid_complete=coverage.hourly_grid_complete,
    )
    assert [item.name for item in primary.metrics] == [
        item.name for item in direct_metrics
    ]
    assert [item.value for item in primary.metrics] == [
        item.value for item in direct_metrics
    ]
    filled = [step for step in session.trace if step["name"] == "fill"]
    assert filled
    assert all(
        step["fill"] is not None and step["ledger"] is not None for step in filled
    )
    encoded = json.dumps(session_payload(session), allow_nan=False)
    assert "NaN" not in encoded
    assert session.protocol_binding == "frozen_rule_parameters"
    assert session.experiment_id != session.policies[0].result.run_id


def test_seed_strategy_and_cost_change_identity_and_gaps_stay_undefined():
    base = run_backtest(
        strategy="momentum",
        lookback_hours=24,
        scenario="sideways",
        seed=1,
        bar_count=64,
        cost="zero",
    )
    seeded = run_backtest(
        strategy="momentum",
        lookback_hours=24,
        scenario="sideways",
        seed=2,
        bar_count=64,
        cost="zero",
    )
    reverted = run_backtest(
        strategy="mean_reversion",
        lookback_hours=12,
        scenario="sideways",
        seed=1,
        bar_count=64,
        cost="zero",
        entry_threshold=Decimal("0.05"),
    )
    assert base.experiment_id != seeded.experiment_id
    assert base.scenario_id != seeded.scenario_id
    assert reverted.protocol_binding == "exploratory"
    gapped = run_backtest(
        strategy="momentum",
        lookback_hours=24,
        scenario="gap",
        seed=5,
        bar_count=64,
        cost="baseline",
    )
    assert len(gapped.policies[0].result.market_gaps) == 1
    sharpe = next(
        item for item in gapped.policies[0].metrics if item.name == "sharpe_ratio"
    )
    assert sharpe.value is None
    assert sharpe.reason is not None


def test_compare_keeps_every_preset_without_a_trace():
    session = run_compare(
        lookback_hours=24,
        entry_threshold=None,
        scenario="shock",
        seed=9,
        bar_count=60,
    )
    assert len(session.policies) == 18
    names = {(item.tied_to, item.name) for item in session.policies}
    assert ("momentum", "cash") in names
    assert ("mean_reversion", "buy_and_hold") in names
    momentum = next(
        item
        for item in session.policies
        if item.tied_to == "momentum" and item.name == "momentum"
    )
    reversion = next(
        item
        for item in session.policies
        if item.tied_to == "mean_reversion" and item.name == "mean_reversion"
    )
    assert momentum.result.run_id != reversion.result.run_id


def test_http_rejects_prices_paths_and_keeps_research_closed():
    client = TestClient(create_app())
    health = client.get("/api/health")
    assert health.status_code == 200
    assert client.get("/health").status_code == 200
    rejected = client.post(
        "/api/backtest",
        json={
            "strategy": "momentum",
            "lookback_hours": 24,
            "scenario": "uptrend",
            "seed": 1,
            "bar_count": 48,
            "cost": "baseline",
            "prices": ["1"],
        },
    )
    assert rejected.status_code == 422
    blocked = client.get("/api/research-block")
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["empirical_acceptance_enabled"] is ACCEPTANCE_ENABLED is False
    assert body["activation_enabled"] is False
    assert body["train"] == "TRAIN_NOT_EXECUTED"
    assert "sharpe" not in body
    assert client.get("/api/research-block?source_path=/tmp/x").status_code == 422
    huge = client.post(
        "/api/backtest",
        content=b'{"strategy":"momentum"}' + b" " * 9000,
        headers={"content-type": "application/json"},
    )
    assert huge.status_code == 413
    ok = client.post(
        "/api/backtest",
        json={
            "strategy": "mean_reversion",
            "lookback_hours": 24,
            "entry_threshold": "0.02",
            "scenario": "sideways",
            "seed": 4,
            "bar_count": 48,
            "cost": "pessimistic_2x",
        },
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["data_origin"] == "synthetic_unverified"
    assert payload["protocol_approved"] is False
    assert payload["trace"]
    assert payload["engine_run_ids"]["mean_reversion"]
