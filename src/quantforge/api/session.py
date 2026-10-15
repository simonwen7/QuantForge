"""Run synthetic terminal sessions on the existing engine.

This module does not reimplement fills, fees, or risk formulas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from quantforge.analytics.benchmarks import (
    buy_and_hold_signals,
    cash_signals,
    entry_fill_time,
)
from quantforge.analytics.models import (
    ANALYTICS_VERSION,
    DEFAULT_SCENARIOS,
    SYNTHETIC_STATEMENT,
    CostScenario,
    MetricResult,
)
from quantforge.analytics.returns import (
    equity_returns,
    performance_sample,
    sample_coverage,
)
from quantforge.analytics.risk import metric_set
from quantforge.analytics.verified_study import StudyPlan, run_empirical_study
from quantforge.backtest import (
    ENGINE_VERSION,
    BacktestEngine,
    BacktestResult,
    ExecutionAssumptions,
)
from quantforge.backtest.events import (
    BAR_COMPLETE,
    BAR_OPEN,
    FILL,
    SIGNAL,
    STALE_VALUATION,
    VALUATION,
)
from quantforge.backtest.models import EquityPoint, Fill, format_decimal, format_utc
from quantforge.research.access import ACCEPTANCE_ENABLED, ResearchScope
from quantforge.research.experiment import experiment_id
from quantforge.research.market_bars import MarketBar
from quantforge.research.models import PriceObservation, ResearchError
from quantforge.research.protocol_freeze import candidate_protocol_freeze
from quantforge.research.readiness import assess_evidence
from quantforge.strategies import (
    BASELINE_ENTRY_THRESHOLD,
    BASELINE_LOOKBACK_HOURS,
    MeanReversionConfig,
    MeanReversionStrategy,
    MomentumConfig,
    MomentumStrategy,
)
from quantforge.synthetic.scenarios import (
    COMPARE_MAX_BARS,
    GENERATOR_VERSION,
    MAX_BARS,
    MAX_LOOKBACK,
    generate_scenario,
    scenario_identity,
)

FROZEN_THRESHOLD = BASELINE_ENTRY_THRESHOLD
_MICROSECOND = timedelta(microseconds=1)


class SessionError(ResearchError):
    """The terminal request cannot be executed."""


@dataclass(frozen=True)
class PolicyRun:
    """One engine pass and the M7 sample derived from it."""

    name: str
    tied_to: str
    result: BacktestResult
    metrics: tuple[MetricResult, ...]
    sample: tuple[EquityPoint, ...]
    hourly_grid_complete: bool


@dataclass(frozen=True)
class SingleSession:
    """A strategy, cash, and buy-and-hold on one generated path."""

    statement: str
    scenario_name: str
    seed: int
    bar_count: int
    scenario_id: str
    strategy: str
    lookback_hours: int
    entry_threshold: Decimal | None
    config_id: str
    protocol_binding: str
    cost: CostScenario
    experiment_id: str
    bars: tuple[MarketBar, ...]
    signals: tuple[object, ...]
    policies: tuple[PolicyRun, ...]
    trace: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class CompareSession:
    """Both rules and their benchmarks. No cell is dropped."""

    statement: str
    scenario_name: str
    seed: int
    bar_count: int
    scenario_id: str
    lookback_hours: int
    entry_threshold: Decimal
    protocol_binding: str
    experiment_id: str
    policies: tuple[PolicyRun, ...]


def run_backtest(
    *,
    strategy: str,
    lookback_hours: int,
    scenario: str,
    seed: int,
    bar_count: int,
    cost: str,
    entry_threshold: Decimal | None = None,
) -> SingleSession:
    """Execute one exploratory or frozen-parameter synthetic session."""
    _require_strategy(strategy)
    if strategy == "momentum" and entry_threshold is not None:
        raise SessionError("momentum does not take an entry threshold")
    threshold = _threshold(entry_threshold)
    _require_room(lookback_hours, bar_count, MAX_BARS)
    chosen = _cost(cost)
    bars = generate_scenario(scenario, seed, bar_count)
    signals = _strategy_signals(strategy, lookback_hours, threshold, bars)
    policies = _family(strategy, signals, bars, chosen)
    binding = _binding(strategy, lookback_hours, threshold)
    config_id = policies[0].result.strategy_config_id
    identity = _experiment_identity(
        scenario_identity(scenario, seed, bar_count),
        config_id,
        binding,
        chosen.name,
        policies,
    )
    primary = policies[0]
    return SingleSession(
        statement=SYNTHETIC_STATEMENT,
        scenario_name=scenario,
        seed=seed,
        bar_count=bar_count,
        scenario_id=scenario_identity(scenario, seed, bar_count),
        strategy=strategy,
        lookback_hours=lookback_hours,
        entry_threshold=None if strategy == "momentum" else threshold,
        config_id=config_id,
        protocol_binding=binding,
        cost=chosen,
        experiment_id=identity,
        bars=bars,
        signals=signals,
        policies=policies,
        trace=tuple(_trace(primary.result, signals)),
    )


def run_compare(
    *,
    lookback_hours: int,
    entry_threshold: Decimal | None,
    scenario: str,
    seed: int,
    bar_count: int,
) -> CompareSession:
    """Run both rules under every preset cost on one path."""
    threshold = _threshold(entry_threshold)
    _require_room(lookback_hours, bar_count, COMPARE_MAX_BARS)
    bars = generate_scenario(scenario, seed, bar_count)
    policies: list[PolicyRun] = []
    for name in ("momentum", "mean_reversion"):
        signals = _strategy_signals(name, lookback_hours, threshold, bars)
        for cost in DEFAULT_SCENARIOS:
            policies.extend(_family(name, signals, bars, cost))
    binding = _binding("mean_reversion", lookback_hours, threshold)
    identity = _experiment_identity(
        scenario_identity(scenario, seed, bar_count),
        "compare",
        binding,
        "all_presets",
        tuple(policies),
    )
    return CompareSession(
        statement=SYNTHETIC_STATEMENT,
        scenario_name=scenario,
        seed=seed,
        bar_count=bar_count,
        scenario_id=scenario_identity(scenario, seed, bar_count),
        lookback_hours=lookback_hours,
        entry_threshold=threshold,
        protocol_binding=binding,
        experiment_id=identity,
        policies=tuple(policies),
    )


def blocked_research_payload() -> dict[str, object]:
    """Call the disabled empirical runner without a path or a reader."""
    study = run_empirical_study(
        StudyPlan(
            protocol=candidate_protocol_freeze(),
            requested_scope=ResearchScope.VERIFIED_HOLDOUT,
            authorization=None,
            preflight=assess_evidence(),
        )
    )
    return {
        "activation_enabled": study.activation_enabled,
        "empirical_acceptance_enabled": ACCEPTANCE_ENABLED,
        "empirical_table_allowed": study.empirical_table_allowed,
        "holdout": study.holdout_state,
        "preflight_id": study.preflight_id,
        "protocol_approved": False,
        "protocol_id": study.protocol_id,
        "requested_scope": study.requested_scope,
        "train": study.train_state,
        "validation": study.validation_state,
        "access_defect": study.access.defect,
        "engine_version": ENGINE_VERSION,
        "analytics_version": ANALYTICS_VERSION,
    }


def _family(
    strategy: str,
    signals: tuple[object, ...],
    bars: tuple[MarketBar, ...],
    cost: CostScenario,
) -> list[PolicyRun]:
    assumptions = ExecutionAssumptions(
        fee_rate=cost.fee_rate,
        slippage_rate=cost.slippage_rate,
    )
    streams = {
        strategy: signals,
        "cash": cash_signals(bars),
        "buy_and_hold": buy_and_hold_signals(bars, signals),
    }
    entry = entry_fill_time(signals)
    engine = BacktestEngine()
    runs: list[PolicyRun] = []
    for name, stream in streams.items():
        result = engine.run_synthetic(bars, stream, assumptions)
        sample = performance_sample(result.equity_points, entry)
        returns = equity_returns(sample, opening_anchor=True)
        coverage = sample_coverage(
            sample,
            returns,
            evaluation_end=sample[-1].time + _MICROSECOND,
        )
        runs.append(
            PolicyRun(
                name=name,
                tied_to=strategy,
                result=result,
                metrics=metric_set(
                    sample,
                    returns,
                    hourly_grid_complete=coverage.hourly_grid_complete,
                ),
                sample=sample,
                hourly_grid_complete=coverage.hourly_grid_complete,
            )
        )
    return runs


def _strategy_signals(
    strategy: str,
    lookback_hours: int,
    threshold: Decimal,
    bars: tuple[MarketBar, ...],
) -> tuple[object, ...]:
    observations = tuple(
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
    if strategy == "momentum":
        return MomentumStrategy(MomentumConfig(lookback_hours=lookback_hours)).generate(
            observations
        )
    return MeanReversionStrategy(
        MeanReversionConfig(lookback_hours=lookback_hours, entry_threshold=threshold)
    ).generate(observations)


def _trace(
    result: BacktestResult, signals: tuple[object, ...]
) -> list[dict[str, object]]:
    by_available = {signal.available_at: signal for signal in signals}
    orders = {order.order_id: order for order in result.orders}
    fills = {fill.order_id: fill for fill in result.fills}
    ledger = {entry.fill_id: entry for entry in result.ledger}
    equity_buckets: dict[object, list[EquityPoint]] = {}
    for point in result.equity_points:
        equity_buckets.setdefault(point.time, []).append(point)
    equity_cursor = {key: 0 for key in equity_buckets}
    stale_buckets: dict[object, list[object]] = {}
    for mark in result.stale_valuations:
        stale_buckets.setdefault(mark.time, []).append(mark)
    stale_cursor = {key: 0 for key in stale_buckets}
    steps: list[dict[str, object]] = []
    for index, event in enumerate(result.events):
        order = orders.get(event.order_id) if event.order_id else None
        fill = (
            fills.get(event.order_id) if event.name == FILL and event.order_id else None
        )
        posted = ledger.get(fill.fill_id) if fill is not None else None
        point = _take(
            equity_buckets, equity_cursor, event.time, event.name == VALUATION
        )
        stale = _take(
            stale_buckets,
            stale_cursor,
            event.time,
            event.name == STALE_VALUATION,
        )
        signal = by_available.get(event.time) if event.name == SIGNAL else None
        steps.append(
            {
                "index": index,
                "time": format_utc(event.time),
                "phase": int(event.phase),
                "name": event.name,
                "order_id": event.order_id,
                "signal": None if signal is None else _signal_payload(signal),
                "order": None if order is None else _order_payload(order),
                "fill": None if fill is None else _fill_payload(fill),
                "ledger": None if posted is None else _ledger_payload(posted),
                "equity": None if point is None else _equity_payload(point),
                "stale": None if stale is None else _stale_payload(stale),
                "open": None
                if event.name != BAR_OPEN or event.open_view is None
                else format_decimal(event.open_view.open),
                "complete": None
                if event.name != BAR_COMPLETE or event.complete_view is None
                else {
                    "open": format_decimal(event.complete_view.open),
                    "high": format_decimal(event.complete_view.high),
                    "low": format_decimal(event.complete_view.low),
                    "close": format_decimal(event.complete_view.close),
                    "volume": format_decimal(event.complete_view.volume),
                },
            }
        )
    return steps


def _take(buckets: dict, cursors: dict, key: object, wanted: bool) -> object | None:
    if not wanted:
        return None
    bucket = buckets.get(key, [])
    cursor = cursors.get(key, 0)
    if cursor >= len(bucket):
        return None
    cursors[key] = cursor + 1
    return bucket[cursor]


def _signal_payload(signal: object) -> dict[str, object]:
    feature_name = "deviation" if hasattr(signal, "deviation") else "momentum"
    feature = getattr(signal, feature_name)
    reason = signal.no_signal_reason
    return {
        "action": signal.action.value,
        "available_at": format_utc(signal.available_at),
        "feature_name": feature_name,
        "feature": None if feature is None else format_decimal(feature),
        "open_time": format_utc(signal.open_time),
        "reason": None if reason is None else reason.value,
    }


def _order_payload(order: object) -> dict[str, object]:
    return {
        "due_time": format_utc(order.due_time),
        "order_id": order.order_id,
        "reason": None if order.reason is None else order.reason.value,
        "side": order.side.value,
        "state": order.state.value,
        "submitted_at": format_utc(order.submitted_at),
    }


def _fill_payload(fill: Fill) -> dict[str, object]:
    return {
        "execution_price": format_decimal(fill.execution_price),
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "posted_fee": format_decimal(fill.posted_fee),
        "quantity": format_decimal(fill.quantity),
        "side": fill.side.value,
        "time": format_utc(fill.time),
    }


def _ledger_payload(entry: object) -> dict[str, object]:
    return {
        "btc_after": format_decimal(entry.btc_after),
        "cash_after": format_decimal(entry.cash_after),
        "cash_delta": format_decimal(entry.cash_delta),
        "fee": format_decimal(entry.fee),
    }


def _equity_payload(point: EquityPoint) -> dict[str, object]:
    return {
        "btc": format_decimal(point.btc),
        "cash": format_decimal(point.cash),
        "equity": format_decimal(point.equity),
        "price": format_decimal(point.price),
        "price_kind": point.price_kind.value,
    }


def _stale_payload(mark: object) -> dict[str, object]:
    return {
        "btc": format_decimal(mark.btc),
        "cash": format_decimal(mark.cash),
        "last_price": format_decimal(mark.last_price),
        "last_price_time": format_utc(mark.last_price_time),
    }


def _experiment_identity(
    scenario_id: str,
    config_id: str,
    binding: str,
    cost_name: str,
    policies: tuple[PolicyRun, ...] | list[PolicyRun],
) -> str:
    return experiment_id(
        {
            "analytics_version": ANALYTICS_VERSION,
            "cost": cost_name,
            "engine_version": ENGINE_VERSION,
            "generator_version": GENERATOR_VERSION,
            "protocol_binding": binding,
            "run_ids": [
                {
                    "cost_fee": format_decimal(item.result.assumptions.fee_rate),
                    "cost_slippage": format_decimal(
                        item.result.assumptions.slippage_rate
                    ),
                    "policy": item.name,
                    "run_id": item.result.run_id,
                    "tied_to": item.tied_to,
                }
                for item in policies
            ],
            "scenario_id": scenario_id,
            "strategy_config_id": config_id,
        }
    )


def _binding(strategy: str, lookback_hours: int, threshold: Decimal) -> str:
    frozen_lookback = lookback_hours == BASELINE_LOOKBACK_HOURS
    frozen_threshold = strategy == "momentum" or threshold == FROZEN_THRESHOLD
    if frozen_lookback and frozen_threshold:
        return "frozen_rule_parameters"
    return "exploratory"


def _require_strategy(strategy: str) -> None:
    if strategy not in {"momentum", "mean_reversion"}:
        raise SessionError("unsupported strategy")


def _require_room(lookback_hours: int, bar_count: int, maximum: int) -> None:
    if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int):
        raise SessionError("lookback_hours must be an integer")
    if lookback_hours < 1 or lookback_hours > MAX_LOOKBACK:
        raise SessionError("lookback_hours must be between 1 and 48")
    if bar_count > maximum:
        raise SessionError(f"bar_count must be at most {maximum}")
    if bar_count < lookback_hours + 8:
        raise SessionError("bar_count must leave at least 8 bars after the lookback")


def _threshold(value: Decimal | None) -> Decimal:
    chosen = FROZEN_THRESHOLD if value is None else value
    if not isinstance(chosen, Decimal) or not chosen.is_finite():
        raise SessionError("entry_threshold must be a finite decimal")
    if chosen <= 0 or chosen > Decimal("0.5"):
        raise SessionError("entry_threshold must be between 0 and 0.5")
    return chosen


def _cost(name: str) -> CostScenario:
    for scenario in DEFAULT_SCENARIOS:
        if scenario.name == name:
            return scenario
    raise SessionError("unsupported cost preset")
