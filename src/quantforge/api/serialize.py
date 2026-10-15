"""JSON shapes for the terminal. Decimals stay strings."""

from __future__ import annotations

from decimal import Decimal

from quantforge.analytics.models import (
    ANALYTICS_VERSION,
    DEFAULT_SCENARIOS,
    SYNTHETIC_STATEMENT,
)
from quantforge.analytics.verified_study import ACTIVATION_ENABLED
from quantforge.api.session import CompareSession, PolicyRun, SingleSession
from quantforge.backtest.models import ENGINE_VERSION, format_decimal, format_utc
from quantforge.research.access import ACCEPTANCE_ENABLED
from quantforge.research.returns import simple_return
from quantforge.synthetic.scenarios import (
    COMPARE_MAX_BARS,
    GENERATOR_VERSION,
    MAX_BARS,
    MAX_LOOKBACK,
    MAX_SEED,
    MIN_BARS,
    MIN_SEED,
)


def config_payload() -> dict[str, object]:
    """Return the bounds the form is allowed to submit."""
    return {
        "analytics_version": ANALYTICS_VERSION,
        "bar_count": {"maximum": MAX_BARS, "minimum": MIN_BARS},
        "compare_bar_count_maximum": COMPARE_MAX_BARS,
        "costs": [
            {
                "fee_rate": format_decimal(item.fee_rate),
                "name": item.name,
                "slippage_rate": format_decimal(item.slippage_rate),
            }
            for item in DEFAULT_SCENARIOS
        ],
        "empirical_acceptance_enabled": ACCEPTANCE_ENABLED,
        "empirical_activation_enabled": ACTIVATION_ENABLED,
        "engine_version": ENGINE_VERSION,
        "generator_version": GENERATOR_VERSION,
        "latency_bars": 1,
        "lookback_hours": {"maximum": MAX_LOOKBACK, "minimum": 1},
        "protocol_approved": False,
        "scenarios": ["uptrend", "downtrend", "sideways", "shock", "gap"],
        "seed": {"maximum": MAX_SEED, "minimum": MIN_SEED},
        "statement": SYNTHETIC_STATEMENT,
        "strategies": ["momentum", "mean_reversion"],
    }


def session_payload(session: SingleSession) -> dict[str, object]:
    """Serialize one backtest, including the primary event trace."""
    return {
        "bars": [_bar_payload(bar) for bar in session.bars],
        "config_id": session.config_id,
        "cost": {
            "fee_rate": format_decimal(session.cost.fee_rate),
            "name": session.cost.name,
            "slippage_rate": format_decimal(session.cost.slippage_rate),
        },
        "data_origin": "synthetic_unverified",
        "engine_run_ids": {item.name: item.result.run_id for item in session.policies},
        "entry_threshold": None
        if session.entry_threshold is None
        else format_decimal(session.entry_threshold),
        "experiment_id": session.experiment_id,
        "latency_bars": 1,
        "lookback_hours": session.lookback_hours,
        "market_gaps": [
            {
                "next_open_time": format_utc(gap.next_open_time),
                "previous_open_time": format_utc(gap.previous_open_time),
            }
            for gap in session.policies[0].result.market_gaps
        ],
        "policies": [_policy_payload(item, detailed=True) for item in session.policies],
        "protocol_approved": False,
        "protocol_binding": session.protocol_binding,
        "scenario": {
            "bar_count": session.bar_count,
            "generator_version": GENERATOR_VERSION,
            "name": session.scenario_name,
            "scenario_id": session.scenario_id,
            "seed": session.seed,
        },
        "signals": [_signal_row(signal) for signal in session.signals],
        "statement": session.statement,
        "strategy": session.strategy,
        "trace": list(session.trace),
    }


def compare_payload(session: CompareSession) -> dict[str, object]:
    """Serialize comparison cells without per-cell event traces."""
    return {
        "data_origin": "synthetic_unverified",
        "entry_threshold": format_decimal(session.entry_threshold),
        "experiment_id": session.experiment_id,
        "lookback_hours": session.lookback_hours,
        "policies": [
            _policy_payload(item, detailed=False) for item in session.policies
        ],
        "protocol_approved": False,
        "protocol_binding": session.protocol_binding,
        "scenario": {
            "bar_count": session.bar_count,
            "generator_version": GENERATOR_VERSION,
            "name": session.scenario_name,
            "scenario_id": session.scenario_id,
            "seed": session.seed,
        },
        "statement": session.statement,
    }


def _policy_payload(item: PolicyRun, *, detailed: bool) -> dict[str, object]:
    peak = item.sample[0].equity
    equity = []
    for point in item.sample:
        peak = max(peak, point.equity)
        equity.append(
            {
                "drawdown": format_decimal(simple_return(peak, point.equity)),
                "equity": format_decimal(point.equity),
                "time": format_utc(point.time),
            }
        )
    payload: dict[str, object] = {
        "cost": {
            "fee_rate": format_decimal(item.result.assumptions.fee_rate),
            "slippage_rate": format_decimal(item.result.assumptions.slippage_rate),
        },
        "equity": equity,
        "fill_count": len(item.result.fills),
        "hourly_grid_complete": item.hourly_grid_complete,
        "metrics": [_metric_payload(metric) for metric in item.metrics],
        "name": item.name,
        "posted_fees": format_decimal(item.result.accumulated_fees),
        "run_id": item.result.run_id,
        "tied_to": item.tied_to,
    }
    if detailed and item.name == item.tied_to:
        payload["fills"] = [
            {
                "execution_price": format_decimal(fill.execution_price),
                "order_id": fill.order_id,
                "posted_fee": format_decimal(fill.posted_fee),
                "side": fill.side.value,
                "time": format_utc(fill.time),
            }
            for fill in item.result.fills
        ]
    return payload


def _metric_payload(metric: object) -> dict[str, object]:
    value = None
    value_hex = None
    if isinstance(metric.value, Decimal):
        value = format_decimal(metric.value)
    elif isinstance(metric.value, float):
        value_hex = float.hex(metric.value)
    return {
        "coverage": metric.coverage,
        "name": metric.name,
        "observation_count": metric.observation_count,
        "reason": None if metric.reason is None else metric.reason.value,
        "unit": metric.unit,
        "value": value,
        "value_hex": value_hex,
    }


def _bar_payload(bar: object) -> dict[str, str]:
    return {
        "close": format_decimal(bar.close),
        "high": format_decimal(bar.high),
        "low": format_decimal(bar.low),
        "open": format_decimal(bar.open),
        "time": format_utc(bar.open_time),
        "volume": format_decimal(bar.volume),
    }


def _signal_row(signal: object) -> dict[str, object]:
    feature_name = "deviation" if hasattr(signal, "deviation") else "momentum"
    feature = getattr(signal, feature_name)
    reason = signal.no_signal_reason
    return {
        "action": signal.action.value,
        "available_at": format_utc(signal.available_at),
        "feature": None if feature is None else format_decimal(feature),
        "feature_name": feature_name,
        "open_time": format_utc(signal.open_time),
        "reason": None if reason is None else reason.value,
    }
