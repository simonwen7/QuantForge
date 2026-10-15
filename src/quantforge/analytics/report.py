"""Synthetic momentum study: nine M6 runs, shared metrics, one report.

The Markdown file is rendered from the canonical JSON payload. It does not
recompute returns or risk figures. Wall-clock time is not part of the report id.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from quantforge.analytics.benchmarks import (
    buy_and_hold_signals,
    cash_signals,
    entry_fill_time,
)
from quantforge.analytics.models import (
    ANALYTICS_VERSION,
    ANNUALIZATION_PERIODS,
    BENCHMARK_ENTRY_POLICY,
    DEFAULT_SCENARIOS,
    MISSING_DATA_POLICY,
    RETURN_CONVENTION,
    RISK_FREE_RATE_HOURLY,
    SORTINO_DENOMINATOR,
    SORTINO_TARGET_HOURLY,
    SYNTHETIC_STATEMENT,
    VALUATION_CONVENTION,
    AnalyticsError,
    CostScenario,
    PolicyResult,
    ResearchReport,
    SampleCoverage,
    ScenarioResult,
)
from quantforge.analytics.returns import (
    equity_returns,
    performance_sample,
    sample_coverage,
)
from quantforge.analytics.risk import metric_set
from quantforge.backtest import BacktestEngine
from quantforge.backtest.models import (
    DEFAULT_INITIAL_CASH,
    DEFAULT_QUANTUM,
    BacktestResult,
    ExecutionAssumptions,
    format_decimal,
    format_utc,
    run_identity,
)
from quantforge.research.experiment import canonical_json
from quantforge.research.market_bars import DataOrigin, MarketBar, synthetic_market_bar
from quantforge.research.models import PriceObservation
from quantforge.research.returns import return_context
from quantforge.strategies import MomentumConfig, MomentumStrategy, strategy_config_id

_HOUR = timedelta(hours=1)
_POLICIES = (
    ("momentum", "m5_momentum"),
    ("cash", "no_orders"),
    ("buy_and_hold", "adapter_not_observed"),
)
_STATIC_WARNINGS = (
    "Fees, slippage, initial capital, and the cash and BTC quanta are synthetic conventions.",
    "8760 is a 365-day reporting convention, not the hour count of every calendar year.",
    "The hourly risk-free rate and the Sortino target are zero.",
    "Sortino divides the root-mean-square shortfall by the full hourly return count.",
    "Valuation uses the bar open when it is observable, otherwise the completed close.",
    "Hourly returns are as-of equity ratios, not a close-to-close price series.",
    "The open mark does not include that bar's later high, low, close, or volume.",
    "Exposure is the share of post-entry marks with positive BTC.",
    "There is no order-book, depth, leverage, borrowing, or short-sale model.",
    "Buy-and-hold LONG carries a contract placeholder, not a measured momentum value.",
    "A passing report does not establish profitability or an exchange fill.",
    "Actual Binance archive compatibility, licensing, and the 2025 holdout remain unresolved.",
)


def demonstration_bars() -> tuple[MarketBar, ...]:
    """Return a short synthetic path that can complete a 24-hour warm-up.

    Prices rise for the lookback and then step down. The path is a fixture for
    the mechanics demonstration, not a market sample.
    """
    start = datetime(2022, 6, 1, tzinfo=UTC)
    bars: list[MarketBar] = []
    for index in range(32):
        price = Decimal(100 + index) if index <= 24 else Decimal(80)
        bars.append(
            synthetic_market_bar(start + index * _HOUR, price, close=price, volume=1)
        )
    return tuple(bars)


def run_synthetic_study(
    bars: Sequence[MarketBar],
    config: MomentumConfig | None = None,
    scenarios: Sequence[CostScenario] = DEFAULT_SCENARIOS,
    initial_cash: Decimal = DEFAULT_INITIAL_CASH,
) -> ResearchReport:
    """Run momentum, cash, and buy-and-hold under each pre-specified cost row."""
    _reject_unusable_bars(bars)
    chosen = config if config is not None else MomentumConfig()
    strategy_signals = MomentumStrategy(chosen).generate(_observations(bars))
    entry = entry_fill_time(strategy_signals)
    streams = {
        "momentum": strategy_signals,
        "cash": cash_signals(bars),
        "buy_and_hold": buy_and_hold_signals(bars, strategy_signals),
    }
    engine = BacktestEngine()
    scenario_results: list[ScenarioResult] = []
    reference_times: tuple[datetime, ...] | None = None
    reference_sample: tuple[datetime, ...] | None = None
    reference_gaps: tuple[object, ...] | None = None
    coverage: SampleCoverage | None = None
    for scenario in scenarios:
        assumptions = ExecutionAssumptions(
            initial_cash=initial_cash,
            fee_rate=scenario.fee_rate,
            slippage_rate=scenario.slippage_rate,
        )
        policies: list[PolicyResult] = []
        for name, role in _POLICIES:
            result = engine.run_synthetic(bars, streams[name], assumptions)
            times = tuple(point.time for point in result.equity_points)
            if reference_times is None:
                reference_times = times
            elif times != reference_times:
                raise AnalyticsError("policies do not share equity timestamps")
            if reference_gaps is None:
                reference_gaps = result.market_gaps
            elif result.market_gaps != reference_gaps:
                raise AnalyticsError("policies do not share the market-gap record")
            sample = performance_sample(result.equity_points, entry)
            sample_times = tuple(point.time for point in sample)
            returns = equity_returns(sample, opening_anchor=True)
            if reference_sample is None:
                reference_sample = sample_times
            elif sample_times != reference_sample:
                raise AnalyticsError("policies do not share the evaluation clock")
            if coverage is None:
                coverage = sample_coverage(
                    sample,
                    returns,
                    evaluation_end=sample[-1].time + timedelta(microseconds=1),
                )
            _require_same_clock(sample, coverage)
            policies.append(
                _policy_result(name, role, result, sample, returns, coverage, entry)
            )
        scenario_results.append(ScenarioResult(scenario, tuple(policies)))
    if coverage is None or reference_gaps is None:
        raise AnalyticsError("a study requires the three pre-specified scenarios")
    built = tuple(scenario_results)
    warnings = _warnings(coverage, built)
    identity = _identity_payload(chosen, initial_cash, coverage, built)
    return ResearchReport(
        report_id=run_identity(identity),
        analytics_version=ANALYTICS_VERSION,
        data_origin=DataOrigin.SYNTHETIC_UNVERIFIED.value,
        statement=SYNTHETIC_STATEMENT,
        strategy_config_id=strategy_config_id(chosen),
        valuation_convention=VALUATION_CONVENTION,
        return_convention=RETURN_CONVENTION,
        missing_data_policy=MISSING_DATA_POLICY,
        benchmark_entry_policy=BENCHMARK_ENTRY_POLICY,
        annualization_periods=ANNUALIZATION_PERIODS,
        risk_free_rate_hourly=RISK_FREE_RATE_HOURLY,
        sortino_target_hourly=SORTINO_TARGET_HOURLY,
        sortino_denominator=SORTINO_DENOMINATOR,
        initial_cash=initial_cash,
        latency_bars=1,
        quantity_quantum=DEFAULT_QUANTUM,
        cash_quantum=DEFAULT_QUANTUM,
        coverage=coverage,
        market_gap_count=len(reference_gaps),
        scenarios=built,
        warnings=warnings,
    )


def report_payload(report: ResearchReport) -> dict[str, object]:
    """Serialize a report with Decimal strings and binary64 hex floats."""
    coverage = report.coverage
    return {
        "analytics_version": report.analytics_version,
        "annualization_periods": report.annualization_periods,
        "benchmark_entry_policy": report.benchmark_entry_policy,
        "cash_quantum": format_decimal(report.cash_quantum),
        "coverage": {
            "equity_count": coverage.equity_count,
            "evaluation_end": format_utc(coverage.evaluation_end),
            "evaluation_start": format_utc(coverage.evaluation_start),
            "gap_count": coverage.gap_count,
            "hourly_grid_complete": coverage.hourly_grid_complete,
            "opening_anchor_time": format_utc(coverage.opening_anchor_time),
            "close_mark_count": coverage.close_mark_count,
            "open_mark_count": coverage.open_mark_count,
            "return_count": coverage.return_count,
            "segments": [
                {
                    "end": format_utc(segment.end),
                    "return_count": segment.return_count,
                    "start": format_utc(segment.start),
                }
                for segment in coverage.segments
            ],
            "short_sample": coverage.short_sample,
        },
        "data_origin": report.data_origin,
        "initial_cash": format_decimal(report.initial_cash),
        "latency_bars": report.latency_bars,
        "market_gap_count": report.market_gap_count,
        "missing_data_policy": report.missing_data_policy,
        "quantity_quantum": format_decimal(report.quantity_quantum),
        "return_convention": report.return_convention,
        "report_id": report.report_id,
        "risk_free_rate_hourly": format_decimal(report.risk_free_rate_hourly),
        "scenarios": [_scenario_payload(item) for item in report.scenarios],
        "sortino_denominator": report.sortino_denominator,
        "sortino_target_hourly": format_decimal(report.sortino_target_hourly),
        "statement": report.statement,
        "strategy_config_id": report.strategy_config_id,
        "valuation_convention": report.valuation_convention,
        "warnings": list(report.warnings),
    }


def render_markdown(report: ResearchReport) -> str:
    """Render the canonical payload. This function does not calculate metrics."""
    payload = report_payload(report)
    lines = [
        "# QuantForge synthetic research report",
        "",
        str(payload["statement"]),
        "",
        f"- Report ID: `{payload['report_id']}`",
        f"- Analytics: `{payload['analytics_version']}`",
        f"- Origin: `{payload['data_origin']}`",
        f"- Strategy configuration: `{payload['strategy_config_id']}`",
        f"- Valuation: `{payload['valuation_convention']}`",
        f"- Return convention: `{payload['return_convention']}`",
        f"- Missing-data policy: `{payload['missing_data_policy']}`",
        f"- Benchmark entry: `{payload['benchmark_entry_policy']}`",
        f"- Initial cash: {payload['initial_cash']}",
        f"- Latency bars: {payload['latency_bars']}",
        f"- Cash quantum: {payload['cash_quantum']}",
        f"- Quantity quantum: {payload['quantity_quantum']}",
        f"- Annualization periods: {payload['annualization_periods']}",
        f"- Hourly risk-free rate: {payload['risk_free_rate_hourly']}",
        f"- Sortino target: {payload['sortino_target_hourly']}",
        f"- Sortino denominator: `{payload['sortino_denominator']}`",
        "",
        "## Coverage",
        "",
    ]
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    for key in (
        "opening_anchor_time",
        "evaluation_start",
        "evaluation_end",
        "equity_count",
        "return_count",
        "gap_count",
        "hourly_grid_complete",
        "open_mark_count",
        "close_mark_count",
        "short_sample",
    ):
        lines.append(f"- {key}: {coverage[key]}")
    lines.extend(["", f"- Market gaps: {payload['market_gap_count']}", ""])
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        lines.extend(
            [
                f"## {scenario['name']}",
                "",
                f"- Fee: {scenario['fee_rate']}",
                f"- Slippage: {scenario['slippage_rate']}",
                "",
            ]
        )
        policies = scenario["policies"]
        assert isinstance(policies, list)
        for policy in policies:
            assert isinstance(policy, dict)
            lines.extend(
                [
                    f"### {policy['name']}",
                    "",
                    f"- Run ID: `{policy['run_id']}`",
                    f"- Signal role: `{policy['signal_role']}`",
                    f"- Fills: {policy['fill_count']}",
                    f"- Posted fees: {policy['posted_fees']}",
                    f"- Exposure fraction: {policy['exposure_fraction']}",
                    f"- Stale valuations: {policy['stale_count']}",
                    "",
                ]
            )
            metrics = policy["metrics"]
            assert isinstance(metrics, list)
            for metric in metrics:
                assert isinstance(metric, dict)
                shown = metric["value"]
                if shown is None and metric["value_hex"] is not None:
                    shown = float.fromhex(str(metric["value_hex"])).hex()
                reason = metric["reason"] if metric["reason"] is not None else "defined"
                lines.append(
                    f"- {metric['name']}: {shown} ({reason}; {metric['coverage']}; "
                    f"n={metric['observation_count']})"
                )
            lines.append("")
    lines.extend(["## Warnings", ""])
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def write_synthetic_demonstration(directory: Path) -> ResearchReport:
    """Write the JSON and Markdown demonstration into ``directory``."""
    report = run_synthetic_study(demonstration_bars())
    directory.mkdir(parents=True, exist_ok=True)
    payload = report_payload(report)
    (directory / "synthetic-study.json").write_text(
        canonical_json(payload) + "\n",
        encoding="utf-8",
    )
    (directory / "synthetic-study.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    return report


def _policy_result(
    name: str,
    role: str,
    result: BacktestResult,
    sample: tuple,
    returns: tuple,
    coverage: SampleCoverage,
    entry: datetime,
) -> PolicyResult:
    held = tuple(point for point in sample if point.time >= entry)
    with localcontext(return_context()):
        exposure = Decimal(sum(point.btc > 0 for point in held)) / Decimal(len(held))
    fees = sum(
        (
            fill.posted_fee
            for fill in result.fills
            if entry <= fill.time < coverage.evaluation_end
        ),
        Decimal(0),
    )
    fills = sum(entry <= fill.time < coverage.evaluation_end for fill in result.fills)
    return PolicyResult(
        name=name,
        strategy_config_id=result.strategy_config_id,
        run_id=result.run_id,
        signal_role=role,
        metrics=metric_set(
            sample,
            returns,
            hourly_grid_complete=coverage.hourly_grid_complete,
        ),
        fill_count=fills,
        posted_fees=fees,
        exposure_fraction=exposure,
        stale_count=len(result.stale_valuations),
    )


def _require_same_clock(sample: tuple, coverage: SampleCoverage) -> None:
    if sample[0].time != coverage.opening_anchor_time:
        raise AnalyticsError("policies do not share the opening anchor")
    if sample[1].time != coverage.evaluation_start:
        raise AnalyticsError("policies do not share the evaluation start")
    if sample[-1].time + timedelta(microseconds=1) != coverage.evaluation_end:
        raise AnalyticsError("policies do not share the evaluation end")


def _warnings(
    coverage: SampleCoverage,
    scenarios: tuple[ScenarioResult, ...],
) -> tuple[str, ...]:
    notes = list(_STATIC_WARNINGS)
    if coverage.short_sample:
        notes.append(
            "The hourly return count is below 24. Annualized ratios extrapolate "
            "a short sample and are not a significance claim."
        )
    if coverage.open_mark_count and coverage.close_mark_count:
        notes.append(
            "The evaluation sample contains both open marks and completed-close "
            "marks. Those returns stay hourly as-of equity ratios."
        )
    if not coverage.hourly_grid_complete:
        notes.append(
            "The hourly grid is incomplete. Sharpe, Sortino, and volatility are "
            "undefined. Endpoint total return and observed drawdown include gaps."
        )
    if any(
        policy.stale_count for scenario in scenarios for policy in scenario.policies
    ):
        notes.append("Stale valuations were recorded and excluded from returns.")
    return tuple(notes)


def _identity_payload(
    config: MomentumConfig,
    initial_cash: Decimal,
    coverage: SampleCoverage,
    scenarios: tuple[ScenarioResult, ...],
) -> dict[str, object]:
    return {
        "analytics_version": ANALYTICS_VERSION,
        "annualization_periods": ANNUALIZATION_PERIODS,
        "benchmark_entry_policy": BENCHMARK_ENTRY_POLICY,
        "cash_quantum": format_decimal(DEFAULT_QUANTUM),
        "data_origin": DataOrigin.SYNTHETIC_UNVERIFIED.value,
        "evaluation_end": format_utc(coverage.evaluation_end),
        "evaluation_start": format_utc(coverage.evaluation_start),
        "initial_cash": format_decimal(initial_cash),
        "latency_bars": 1,
        "missing_data_policy": MISSING_DATA_POLICY,
        "return_convention": RETURN_CONVENTION,
        "opening_anchor_time": format_utc(coverage.opening_anchor_time),
        "quantity_quantum": format_decimal(DEFAULT_QUANTUM),
        "risk_free_rate_hourly": format_decimal(RISK_FREE_RATE_HOURLY),
        "run_ids": [
            {
                "policy": policy.name,
                "run_id": policy.run_id,
                "scenario": scenario.scenario.name,
            }
            for scenario in scenarios
            for policy in scenario.policies
        ],
        "scenarios": [
            {
                "fee_rate": format_decimal(scenario.scenario.fee_rate),
                "name": scenario.scenario.name,
                "slippage_rate": format_decimal(scenario.scenario.slippage_rate),
            }
            for scenario in scenarios
        ],
        "sortino_denominator": SORTINO_DENOMINATOR,
        "sortino_target_hourly": format_decimal(SORTINO_TARGET_HOURLY),
        "strategy_config_id": strategy_config_id(config),
        "valuation_convention": VALUATION_CONVENTION,
    }


def _scenario_payload(result: ScenarioResult) -> dict[str, object]:
    return {
        "fee_rate": format_decimal(result.scenario.fee_rate),
        "name": result.scenario.name,
        "policies": [_policy_payload(policy) for policy in result.policies],
        "slippage_rate": format_decimal(result.scenario.slippage_rate),
    }


def _policy_payload(policy: PolicyResult) -> dict[str, object]:
    exposure = (
        None
        if policy.exposure_fraction is None
        else format_decimal(policy.exposure_fraction)
    )
    return {
        "exposure_fraction": exposure,
        "fill_count": policy.fill_count,
        "metrics": [_metric_payload(metric) for metric in policy.metrics],
        "name": policy.name,
        "posted_fees": format_decimal(policy.posted_fees),
        "run_id": policy.run_id,
        "signal_role": policy.signal_role,
        "stale_count": policy.stale_count,
        "strategy_config_id": policy.strategy_config_id,
    }


def _metric_payload(metric) -> dict[str, object]:
    value: str | None = None
    value_hex: str | None = None
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


def _observations(bars: Sequence[MarketBar]) -> tuple[PriceObservation, ...]:
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


def _reject_unusable_bars(bars: Sequence[MarketBar]) -> None:
    if not bars:
        raise AnalyticsError("a synthetic study requires market bars")
    previous: datetime | None = None
    for bar in bars:
        if bar.origin is not DataOrigin.SYNTHETIC_UNVERIFIED:
            raise AnalyticsError("the synthetic study rejects verified market data")
        if bar.open_time.year >= 2025 or bar.source_year >= 2025:
            raise AnalyticsError("the synthetic study rejects 2025 holdout timestamps")
        if previous is not None and bar.open_time <= previous:
            raise AnalyticsError("synthetic bars must be strictly chronological")
        previous = bar.open_time
