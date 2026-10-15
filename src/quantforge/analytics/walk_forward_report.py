"""Fixed-rule chronological walk-forward study.

Folds are separate M6 runs. Their equity curves are not stitched, and the
grid is not searched for a winner. Synthetic bars prove mechanics only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from quantforge.analytics.benchmarks import buy_and_hold_signals, cash_signals
from quantforge.analytics.models import (
    ANNUALIZATION_PERIODS,
    DEFAULT_SCENARIOS,
    MISSING_DATA_POLICY,
    RETURN_CONVENTION,
    RISK_FREE_RATE_HOURLY,
    SORTINO_DENOMINATOR,
    SORTINO_TARGET_HOURLY,
    VALUATION_CONVENTION,
    AnalyticsError,
    CostScenario,
    MetricResult,
    PolicyResult,
    SampleCoverage,
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
    EvaluationWindow,
    ExecutionAssumptions,
    format_decimal,
    format_utc,
    run_identity,
)
from quantforge.research.experiment import canonical_json
from quantforge.research.market_bars import DataOrigin, MarketBar, synthetic_market_bar
from quantforge.research.models import PriceObservation
from quantforge.research.protocol import VALIDATION_START
from quantforge.research.returns import return_context
from quantforge.research.walk_forward import (
    CalendarKind,
    WalkForwardCalendar,
    WalkForwardFold,
    injected_calendar,
)
from quantforge.strategies import (
    MomentumConfig,
    MomentumStrategy,
    strategy_config_id,
)
from quantforge.strategies.mean_reversion import (
    BASELINE_ENTRY_THRESHOLD,
    BASELINE_LOOKBACK_HOURS,
    MEAN_REVERSION_FORMULA,
    MeanReversionConfig,
    MeanReversionSignal,
    MeanReversionStrategy,
    mean_reversion_config_id,
)
from quantforge.strategies.models import (
    LookbackSource,
    NoSignalReason,
    SignalAction,
    StrategySignal,
)

WALK_FORWARD_VERSION = "m8-walk-forward-v1"
METHODOLOGY = "fixed_rule_chronological_walk_forward_evaluation"
SELECTION_POLICY = "fixed_rule_no_parameter_selection"
WALK_FORWARD_STATEMENT = (
    "SYNTHETIC FIXED-RULE WALK-FORWARD. This is not BTCUSDT performance "
    "evidence, not a selected configuration, and not a stitched multi-fold "
    "equity curve."
)
FUTURE_SELECTION_NOTE = (
    "A future training-only selector would need a frozen grid, an unchanged "
    "first fold, at least three prior folds with defined total returns, no "
    "Sharpe ranking, and no refit after seeing 2024. That selector is not "
    "implemented."
)
SENSITIVITY_LOOKBACKS = (12, 24, 48)
SENSITIVITY_THRESHOLDS = (Decimal("0.01"), Decimal("0.02"), Decimal("0.05"))
_HOUR = timedelta(hours=1)
_POLICIES = (
    ("momentum", "m5_momentum"),
    ("mean_reversion", "m8_relative_mean_reversion"),
    ("cash", "no_orders"),
    ("buy_and_hold", "adapter_not_observed"),
)
_MECHANICS_START = datetime(2022, 6, 1, tzinfo=UTC)
_MECHANICS_CONTEXT = 48
_MECHANICS_EVAL_HOURS = 6


def baseline_outcome_count(fold_count: int) -> int:
    """Return 4 policies by 3 cost rows by the fold count."""
    return fold_count * len(_POLICIES) * len(DEFAULT_SCENARIOS)


def sensitivity_outcome_count(fold_count: int) -> int:
    """Return the full mean-reversion grid size for this many folds."""
    return (
        fold_count
        * len(SENSITIVITY_LOOKBACKS)
        * len(SENSITIVITY_THRESHOLDS)
        * len(DEFAULT_SCENARIOS)
    )


@dataclass(frozen=True)
class PolicyEvaluation:
    """One policy outcome, including the pre-trade anchor state."""

    result: PolicyResult
    anchor_cash: Decimal
    anchor_btc: Decimal
    final_cash: Decimal
    final_btc: Decimal


@dataclass(frozen=True)
class FoldScenarioResult:
    """The four policies under one predetermined cost row."""

    scenario: CostScenario
    policies: tuple[PolicyEvaluation, ...]


@dataclass(frozen=True)
class FoldEvaluation:
    """Independent fold results. These equity curves are not compounded."""

    fold: WalkForwardFold
    fold_id: str
    scenarios: tuple[FoldScenarioResult, ...]
    coverage: SampleCoverage
    market_gap_count: int
    anchor_time: datetime
    entry_time: datetime


@dataclass(frozen=True)
class SensitivityCell:
    """One reported grid point. It is not a candidate for selection."""

    fold_name: str
    lookback_hours: int
    entry_threshold: Decimal
    scenario: CostScenario
    strategy_config_id: str
    run_id: str
    cell_id: str
    fill_count: int
    posted_fees: Decimal
    metrics: tuple[MetricResult, ...]
    hourly_grid_complete: bool
    market_gap_count: int
    anchor_time: datetime
    entry_time: datetime


@dataclass(frozen=True)
class WalkForwardStudy:
    """Deterministic synthetic walk-forward report. The id excludes wall time."""

    study_id: str
    analytics_version: str
    statement: str
    calendar: WalkForwardCalendar
    baseline_config: MeanReversionConfig
    momentum_config_id: str
    folds: tuple[FoldEvaluation, ...]
    sensitivity: tuple[SensitivityCell, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.analytics_version != WALK_FORWARD_VERSION:
            raise AnalyticsError("walk-forward analytics version is not supported")


def injected_mechanics_calendar() -> WalkForwardCalendar:
    """Return four short folds. This is not the production semiannual calendar."""
    folds: list[WalkForwardFold] = []
    for index in range(4):
        evaluation_start = _MECHANICS_START + timedelta(
            hours=_MECHANICS_CONTEXT + index * _MECHANICS_EVAL_HOURS
        )
        folds.append(
            WalkForwardFold(
                f"fold-{index + 1}",
                _MECHANICS_START,
                evaluation_start,
                evaluation_start,
                evaluation_start + timedelta(hours=_MECHANICS_EVAL_HOURS),
            )
        )
    return injected_calendar("injected-synthetic-mechanics-v1", tuple(folds))


def injected_mechanics_bars() -> tuple[MarketBar, ...]:
    """Return the short path used to exercise the injected mechanics calendar.

    Isolated dips are below the 2% rule and above the 5% rule. They are a
    software fixture, not a market regime sample.
    """
    calendar = injected_mechanics_calendar()
    dips = {
        instant
        for fold in calendar.folds
        for instant in (
            fold.evaluation_start,
            fold.evaluation_start + timedelta(hours=4),
        )
    }
    bars: list[MarketBar] = []
    cursor = _MECHANICS_START
    while cursor < calendar.folds[-1].evaluation_end:
        price = Decimal(97) if cursor in dips else Decimal(100)
        bars.append(synthetic_market_bar(cursor, price, close=price))
        cursor += _HOUR
    return tuple(bars)


def run_walk_forward_study(
    bars: Sequence[MarketBar],
    calendar: WalkForwardCalendar,
    *,
    config: MeanReversionConfig | None = None,
    scenarios: Sequence[CostScenario] = DEFAULT_SCENARIOS,
    allow_gaps: bool = False,
    include_sensitivity: bool = True,
    initial_cash: Decimal = DEFAULT_INITIAL_CASH,
) -> WalkForwardStudy:
    """Evaluate the frozen rules on each fold, then report the full grid."""
    _reject_unusable_bars(bars)
    if not scenarios:
        raise AnalyticsError("a study requires at least one cost scenario")
    if calendar.kind is CalendarKind.PRODUCTION and allow_gaps:
        raise AnalyticsError("production coverage cannot be waived")
    chosen = config if config is not None else MeanReversionConfig()
    momentum = MomentumConfig()
    fold_results: list[FoldEvaluation] = []
    for fold in calendar.folds:
        fold_results.append(
            _evaluate_fold(
                bars,
                calendar,
                fold,
                chosen,
                momentum,
                scenarios,
                allow_gaps=allow_gaps,
                initial_cash=initial_cash,
            )
        )
    cells: list[SensitivityCell] = []
    if include_sensitivity:
        for fold in calendar.folds:
            for lookback in SENSITIVITY_LOOKBACKS:
                for threshold in SENSITIVITY_THRESHOLDS:
                    cell_config = MeanReversionConfig(lookback, threshold)
                    for scenario in scenarios:
                        cells.append(
                            _sensitivity_cell(
                                bars,
                                calendar,
                                fold,
                                cell_config,
                                scenario,
                                allow_gaps=allow_gaps,
                                initial_cash=initial_cash,
                            )
                        )
    built_folds = tuple(fold_results)
    built_cells = tuple(cells)
    warnings = _warnings(calendar, chosen, scenarios, built_folds, built_cells)
    study_id = run_identity(
        _study_identity(calendar, chosen, momentum, built_folds, built_cells)
    )
    return WalkForwardStudy(
        study_id=study_id,
        analytics_version=WALK_FORWARD_VERSION,
        statement=WALK_FORWARD_STATEMENT,
        calendar=calendar,
        baseline_config=chosen,
        momentum_config_id=strategy_config_id(momentum),
        folds=built_folds,
        sensitivity=built_cells,
        warnings=warnings,
    )


def study_payload(study: WalkForwardStudy) -> dict[str, object]:
    """Serialize the study. Markdown reads this payload and does not recompute."""
    return {
        "analytics_version": study.analytics_version,
        "baseline": {
            "entry_threshold": format_decimal(study.baseline_config.entry_threshold),
            "formula": MEAN_REVERSION_FORMULA,
            "lookback_hours": study.baseline_config.lookback_hours,
            "strategy_config_id": mean_reversion_config_id(study.baseline_config),
        },
        "calendar": {
            "folds": [
                {
                    "evaluation_end": format_utc(fold.evaluation_end),
                    "evaluation_start": format_utc(fold.evaluation_start),
                    "history_end": format_utc(fold.history_end),
                    "history_start": format_utc(fold.history_start),
                    "name": fold.name,
                }
                for fold in study.calendar.folds
            ],
            "id": study.calendar.calendar_id,
            "kind": study.calendar.kind.value,
        },
        "data_origin": DataOrigin.SYNTHETIC_UNVERIFIED.value,
        "folds": [_fold_payload(fold) for fold in study.folds],
        "methodology": METHODOLOGY,
        "momentum_config_id": study.momentum_config_id,
        "selection_policy": SELECTION_POLICY,
        "sensitivity": [_cell_payload(cell) for cell in study.sensitivity],
        "statement": study.statement,
        "study_id": study.study_id,
        "valuation_convention": VALUATION_CONVENTION,
        "return_convention": RETURN_CONVENTION,
        "warnings": list(study.warnings),
    }


def render_markdown(study: WalkForwardStudy) -> str:
    """Render Markdown from the canonical payload without new calculations."""
    payload = study_payload(study)
    baseline = payload["baseline"]
    calendar = payload["calendar"]
    assert isinstance(baseline, dict)
    assert isinstance(calendar, dict)
    lines = [
        "# QuantForge fixed-rule walk-forward study",
        "",
        str(payload["statement"]),
        "",
        f"- Study ID: `{payload['study_id']}`",
        f"- Analytics: `{payload['analytics_version']}`",
        f"- Methodology: `{payload['methodology']}`",
        f"- Selection: `{payload['selection_policy']}`",
        f"- Origin: `{payload['data_origin']}`",
        f"- Calendar: `{calendar['id']}` ({calendar['kind']})",
        f"- Formula: `{baseline['formula']}`",
        f"- Baseline lookback: {baseline['lookback_hours']}",
        f"- Baseline threshold: {baseline['entry_threshold']}",
        f"- Mean-reversion configuration: `{baseline['strategy_config_id']}`",
        f"- Momentum configuration: `{payload['momentum_config_id']}`",
        f"- Valuation: `{payload['valuation_convention']}`",
        f"- Return convention: `{payload['return_convention']}`",
        "",
        "Fold results are independent. They are not one compounded equity curve.",
        "",
        "## Baseline folds",
        "",
    ]
    fold_payloads = payload["folds"]
    assert isinstance(fold_payloads, list)
    for fold in fold_payloads:
        assert isinstance(fold, dict)
        lines.extend(
            [
                f"### {fold['name']}",
                "",
                f"- Fold ID: `{fold['fold_id']}`",
                f"- History: [{fold['history_start']}, {fold['history_end']})",
                f"- Evaluation: [{fold['evaluation_start']}, {fold['evaluation_end']})",
                f"- Anchor: {fold['anchor_time']}",
                f"- Entry: {fold['entry_time']}",
                f"- Market gaps: {fold['market_gap_count']}",
                f"- Hourly grid complete: {fold['hourly_grid_complete']}",
                "",
            ]
        )
        scenarios = fold["scenarios"]
        assert isinstance(scenarios, list)
        for scenario in scenarios:
            assert isinstance(scenario, dict)
            lines.append(
                f"#### {scenario['name']} fee {scenario['fee_rate']} "
                f"slippage {scenario['slippage_rate']}"
            )
            lines.append("")
            policies = scenario["policies"]
            assert isinstance(policies, list)
            for policy in policies:
                assert isinstance(policy, dict)
                lines.extend(_policy_lines(policy))
    lines.extend(["## Sensitivity", ""])
    cells = payload["sensitivity"]
    assert isinstance(cells, list)
    if not cells:
        lines.append("Sensitivity was not requested for this run.")
        lines.append("")
    for cell in cells:
        assert isinstance(cell, dict)
        lines.append(
            "- "
            f"{cell['fold_name']} L={cell['lookback_hours']} "
            f"threshold={cell['entry_threshold']} {cell['scenario']}: "
            f"anchor={cell['anchor_time']} entry={cell['entry_time']} "
            f"fills={cell['fill_count']} fees={cell['posted_fees']} "
            f"gaps={cell['market_gap_count']} "
            f"grid_complete={cell['hourly_grid_complete']} "
            f"run=`{cell['run_id']}`"
        )
        metrics = cell["metrics"]
        assert isinstance(metrics, list)
        for metric in metrics:
            assert isinstance(metric, dict)
            lines.append(f"  - {_metric_line(metric)}")
    lines.extend(["", "## Warnings", ""])
    warnings = payload["warnings"]
    assert isinstance(warnings, list)
    lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def write_walk_forward_study(directory: Path, study: WalkForwardStudy) -> None:
    """Write JSON and Markdown only into the supplied directory."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = study_payload(study)
    (directory / "walk-forward-study.json").write_text(
        canonical_json(payload) + "\n",
        encoding="utf-8",
    )
    (directory / "walk-forward-study.md").write_text(
        render_markdown(study),
        encoding="utf-8",
    )


def _evaluate_fold(
    bars: Sequence[MarketBar],
    calendar: WalkForwardCalendar,
    fold: WalkForwardFold,
    config: MeanReversionConfig,
    momentum: MomentumConfig,
    scenarios: Sequence[CostScenario],
    *,
    allow_gaps: bool,
    initial_cash: Decimal,
) -> FoldEvaluation:
    lookback = max(config.lookback_hours, momentum.lookback_hours)
    feature, engine = _fold_bars(
        bars,
        fold,
        lookback,
        allow_gaps=allow_gaps,
        label=fold.name,
    )
    observations = _observations(feature)
    mean_signals = _replay_signals(
        MeanReversionStrategy(config).generate(observations),
        engine,
        fold,
        lambda bar: _mean_abstain(bar, config),
    )
    momentum_signals = _replay_signals(
        MomentumStrategy(momentum).generate(observations),
        engine,
        fold,
        lambda bar: _momentum_abstain(bar, momentum),
    )
    _require_entry_signal(mean_signals, fold)
    _require_entry_signal(momentum_signals, fold)
    streams = {
        "momentum": momentum_signals,
        "mean_reversion": mean_signals,
        "cash": cash_signals(engine),
        "buy_and_hold": buy_and_hold_signals(engine, mean_signals),
    }
    entry = fold.evaluation_start + 2 * _HOUR
    anchor = fold.evaluation_start + _HOUR
    window = EvaluationWindow(fold.evaluation_start, fold.evaluation_end)
    engine_runner = BacktestEngine()
    scenario_results: list[FoldScenarioResult] = []
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
        policies: list[PolicyEvaluation] = []
        for name, role in _POLICIES:
            result = engine_runner.run_synthetic(
                engine, streams[name], assumptions, window
            )
            times = tuple(point.time for point in result.equity_points)
            if reference_times is None:
                reference_times = times
            elif times != reference_times:
                raise AnalyticsError("policies do not share equity timestamps")
            if reference_gaps is None:
                reference_gaps = result.market_gaps
            elif result.market_gaps != reference_gaps:
                raise AnalyticsError("policies do not share the market-gap record")
            sample = performance_sample(
                result.equity_points,
                entry,
                split_start=fold.evaluation_start,
            )
            if sample[0].time != anchor or sample[1].time != entry:
                raise AnalyticsError("fold entry anchor is not the pre-trade mark")
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
            policies.append(
                _policy_evaluation(name, role, result, sample, returns, coverage, entry)
            )
        scenario_results.append(FoldScenarioResult(scenario, tuple(policies)))
    if coverage is None or reference_gaps is None:
        raise AnalyticsError("a fold requires a cost scenario")
    built = tuple(scenario_results)
    return FoldEvaluation(
        fold=fold,
        fold_id=run_identity(
            _fold_identity(calendar, fold, feature, built, coverage, reference_gaps)
        ),
        scenarios=built,
        coverage=coverage,
        market_gap_count=len(reference_gaps),
        anchor_time=anchor,
        entry_time=entry,
    )


def _sensitivity_cell(
    bars: Sequence[MarketBar],
    calendar: WalkForwardCalendar,
    fold: WalkForwardFold,
    config: MeanReversionConfig,
    scenario: CostScenario,
    *,
    allow_gaps: bool,
    initial_cash: Decimal,
) -> SensitivityCell:
    feature, engine = _fold_bars(
        bars,
        fold,
        config.lookback_hours,
        allow_gaps=allow_gaps,
        label=f"{fold.name} sensitivity",
    )
    signals = _replay_signals(
        MeanReversionStrategy(config).generate(_observations(feature)),
        engine,
        fold,
        lambda bar: _mean_abstain(bar, config),
    )
    _require_entry_signal(signals, fold)
    entry = fold.evaluation_start + 2 * _HOUR
    result = BacktestEngine().run_synthetic(
        engine,
        signals,
        ExecutionAssumptions(
            initial_cash=initial_cash,
            fee_rate=scenario.fee_rate,
            slippage_rate=scenario.slippage_rate,
        ),
        EvaluationWindow(fold.evaluation_start, fold.evaluation_end),
    )
    sample = performance_sample(
        result.equity_points,
        entry,
        split_start=fold.evaluation_start,
    )
    returns = equity_returns(sample, opening_anchor=True)
    coverage = sample_coverage(
        sample,
        returns,
        evaluation_end=sample[-1].time + timedelta(microseconds=1),
    )
    evaluation = _policy_evaluation(
        "mean_reversion",
        "m8_relative_mean_reversion",
        result,
        sample,
        returns,
        coverage,
        entry,
    )
    identity = {
        "analytics_version": WALK_FORWARD_VERSION,
        "anchor_time": format_utc(sample[0].time),
        "bar_identity": _bars_identity(feature),
        "entry_threshold": format_decimal(config.entry_threshold),
        "entry_time": format_utc(entry),
        "evaluation_end": format_utc(fold.evaluation_end),
        "evaluation_start": format_utc(fold.evaluation_start),
        "fee_rate": format_decimal(scenario.fee_rate),
        "fold_name": fold.name,
        "lookback_hours": config.lookback_hours,
        "missing_data_policy": MISSING_DATA_POLICY,
        "return_convention": RETURN_CONVENTION,
        "run_id": result.run_id,
        "scenario": scenario.name,
        "slippage_rate": format_decimal(scenario.slippage_rate),
        "strategy_config_id": result.strategy_config_id,
        "valuation_convention": VALUATION_CONVENTION,
    }
    return SensitivityCell(
        fold_name=fold.name,
        lookback_hours=config.lookback_hours,
        entry_threshold=config.entry_threshold,
        scenario=scenario,
        strategy_config_id=result.strategy_config_id,
        run_id=result.run_id,
        cell_id=run_identity(identity),
        fill_count=evaluation.result.fill_count,
        posted_fees=evaluation.result.posted_fees,
        metrics=evaluation.result.metrics,
        hourly_grid_complete=coverage.hourly_grid_complete,
        market_gap_count=len(result.market_gaps),
        anchor_time=sample[0].time,
        entry_time=entry,
    )


def _fold_bars(
    bars: Sequence[MarketBar],
    fold: WalkForwardFold,
    lookback_hours: int,
    *,
    allow_gaps: bool,
    label: str,
) -> tuple[tuple[MarketBar, ...], tuple[MarketBar, ...]]:
    if fold.evaluation_end < fold.evaluation_start + 3 * _HOUR:
        raise AnalyticsError(
            f"{label} cannot contain the pre-trade anchor and the latency-one fill"
        )
    feature_start = fold.evaluation_start - timedelta(hours=lookback_hours)
    protected_end = fold.evaluation_start + 3 * _HOUR
    by_time = {bar.open_time: bar for bar in bars}
    cursor = feature_start
    while cursor < protected_end:
        if cursor not in by_time:
            raise AnalyticsError(
                f"{label} lacks the causal context or entry bars required at {cursor}"
            )
        cursor += _HOUR
    feature: list[MarketBar] = []
    missing = 0
    cursor = feature_start
    while cursor < fold.evaluation_end:
        bar = by_time.get(cursor)
        if bar is None:
            missing += 1
        else:
            feature.append(bar)
        cursor += _HOUR
    if missing and not allow_gaps:
        raise AnalyticsError(f"{label} is missing {missing} hourly bars")
    engine = tuple(
        bar for bar in feature if bar.open_time >= fold.evaluation_start - _HOUR
    )
    if not engine:
        raise AnalyticsError(f"{label} has no evaluation bars")
    return tuple(feature), engine


def _replay_signals(generated, engine_bars, fold, abstain):
    by_open = {signal.open_time: signal for signal in generated}
    replay = []
    for bar in engine_bars:
        if bar.open_time < fold.evaluation_start:
            replay.append(abstain(bar))
            continue
        signal = by_open.get(bar.open_time)
        if signal is None:
            raise AnalyticsError("an evaluation bar has no signal")
        if signal.open_time < fold.evaluation_start:
            raise AnalyticsError("a pre-fold decision was passed to the fold replay")
        replay.append(signal)
    return tuple(replay)


def _require_entry_signal(signals, fold: WalkForwardFold) -> None:
    matched = [
        signal for signal in signals if signal.open_time == fold.evaluation_start
    ]
    if len(matched) != 1 or matched[0].action is SignalAction.NO_SIGNAL:
        raise AnalyticsError(
            "the fold start has no directional decision, so the entry was not moved"
        )
    if matched[0].available_at != fold.evaluation_start + _HOUR:
        raise AnalyticsError("the fold-start signal is not available one hour later")
    if matched[0].earliest_eligible_open != matched[0].available_at:
        raise AnalyticsError("the fold-start signal has no next-bar intent")


def _policy_evaluation(name, role, result, sample, returns, coverage, entry):
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
    anchor = sample[0]
    return PolicyEvaluation(
        result=PolicyResult(
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
        ),
        anchor_cash=anchor.cash,
        anchor_btc=anchor.btc,
        final_cash=result.final_cash,
        final_btc=result.final_btc,
    )


def _mean_abstain(bar: MarketBar, config: MeanReversionConfig) -> MeanReversionSignal:
    return MeanReversionSignal(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        action=SignalAction.NO_SIGNAL,
        deviation=None,
        no_signal_reason=NoSignalReason.WARMUP,
        strategy_config_id=mean_reversion_config_id(config),
        lookback_hours=config.lookback_hours,
        entry_threshold=config.entry_threshold,
        lookback_start=bar.open_time,
        lookback_end=bar.open_time,
        sources=(_bar_source(bar),),
        execution_policy=None,
        earliest_eligible_open=None,
    )


def _momentum_abstain(bar: MarketBar, config: MomentumConfig) -> StrategySignal:
    return StrategySignal(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        action=SignalAction.NO_SIGNAL,
        momentum=None,
        no_signal_reason=NoSignalReason.WARMUP,
        strategy_config_id=strategy_config_id(config),
        lookback_hours=config.lookback_hours,
        lookback_start=bar.open_time,
        lookback_end=bar.open_time,
        sources=(_bar_source(bar),),
        execution_policy=None,
        earliest_eligible_open=None,
    )


def _bar_source(bar: MarketBar) -> LookbackSource:
    return LookbackSource(
        open_time=bar.open_time,
        source_year=bar.source_year,
        source_month=bar.source_month,
        source_archive_sha256=bar.source_archive_sha256,
        source_schema_version=bar.source_schema_version,
    )


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
        raise AnalyticsError("a walk-forward study requires market bars")
    previous: datetime | None = None
    for bar in bars:
        if bar.origin is not DataOrigin.SYNTHETIC_UNVERIFIED:
            raise AnalyticsError("walk-forward rejects verified market data")
        if (
            bar.open_time >= VALIDATION_START
            or bar.open_time.year >= 2025
            or bar.source_year >= 2025
        ):
            raise AnalyticsError(
                "walk-forward rejects 2024 validation and 2025 holdout timestamps"
            )
        if previous is not None and bar.open_time <= previous:
            raise AnalyticsError("walk-forward bars must be strictly chronological")
        previous = bar.open_time


def _bars_identity(bars: Sequence[MarketBar]) -> str:
    return run_identity(
        {
            "bars": [
                {
                    "close": format_decimal(bar.close),
                    "close_time": format_utc(bar.close_time),
                    "high": format_decimal(bar.high),
                    "low": format_decimal(bar.low),
                    "open": format_decimal(bar.open),
                    "open_time": format_utc(bar.open_time),
                    "source_archive_sha256": bar.source_archive_sha256,
                    "source_month": bar.source_month,
                    "source_schema_version": bar.source_schema_version,
                    "source_year": bar.source_year,
                    "volume": format_decimal(bar.volume),
                }
                for bar in bars
            ]
        }
    )


def _fold_identity(
    calendar, fold, feature, scenarios, coverage, gaps
) -> dict[str, object]:
    return {
        "analytics_version": WALK_FORWARD_VERSION,
        "anchor_time": format_utc(coverage.opening_anchor_time),
        "bar_identity": _bars_identity(feature),
        "calendar_id": calendar.calendar_id,
        "calendar_kind": calendar.kind.value,
        "data_origin": DataOrigin.SYNTHETIC_UNVERIFIED.value,
        "evaluation_end": format_utc(fold.evaluation_end),
        "evaluation_start": format_utc(fold.evaluation_start),
        "history_end": format_utc(fold.history_end),
        "history_start": format_utc(fold.history_start),
        "market_gap_count": len(gaps),
        "missing_data_policy": MISSING_DATA_POLICY,
        "return_convention": RETURN_CONVENTION,
        "runs": [
            {
                "fee_rate": format_decimal(scenario.scenario.fee_rate),
                "policy": policy.result.name,
                "run_id": policy.result.run_id,
                "scenario": scenario.scenario.name,
                "slippage_rate": format_decimal(scenario.scenario.slippage_rate),
                "strategy_config_id": policy.result.strategy_config_id,
            }
            for scenario in scenarios
            for policy in scenario.policies
        ],
        "valuation_convention": VALUATION_CONVENTION,
    }


def _study_identity(calendar, config, momentum, folds, cells) -> dict[str, object]:
    return {
        "analytics_version": WALK_FORWARD_VERSION,
        "annualization_periods": ANNUALIZATION_PERIODS,
        "baseline_strategy_config_id": mean_reversion_config_id(config),
        "calendar_id": calendar.calendar_id,
        "calendar_kind": calendar.kind.value,
        "data_origin": DataOrigin.SYNTHETIC_UNVERIFIED.value,
        "fold_ids": [fold.fold_id for fold in folds],
        "methodology": METHODOLOGY,
        "missing_data_policy": MISSING_DATA_POLICY,
        "momentum_strategy_config_id": strategy_config_id(momentum),
        "no_selection": True,
        "return_convention": RETURN_CONVENTION,
        "risk_free_rate_hourly": format_decimal(RISK_FREE_RATE_HOURLY),
        "selection_policy": SELECTION_POLICY,
        "sensitivity_grid": {
            "lookbacks": list(SENSITIVITY_LOOKBACKS),
            "thresholds": [format_decimal(value) for value in SENSITIVITY_THRESHOLDS],
        },
        "sensitivity_ids": [cell.cell_id for cell in cells],
        "sortino_denominator": SORTINO_DENOMINATOR,
        "sortino_target_hourly": format_decimal(SORTINO_TARGET_HOURLY),
        "statement": WALK_FORWARD_STATEMENT,
        "valuation_convention": VALUATION_CONVENTION,
    }


def _warnings(calendar, config, scenarios, folds, cells) -> tuple[str, ...]:
    notes = [
        "Fees, slippage, initial capital, and quanta are synthetic conventions.",
        "The 2% threshold and 24-hour lookback are pre-specified conventions, not fitted values.",
        "A persistent decline can keep relative mean reversion long. The baseline is not adjusted for that.",
        "8760 is a 365-day reporting convention, not a significance claim.",
        "Hourly returns are as-of equity ratios, not a close-to-close price series.",
        "Each fold starts from its own 10000 USDT and zero BTC.",
        "Fold equity curves are independent resets and are not compounded across folds.",
        "No configuration in the sensitivity matrix is selected or ranked.",
        "The sensitivity cell with lookback 24 and threshold 0.02 repeats the baseline mean-reversion run. It is not a second confirmation.",
        "Sensitivity lookbacks share each fold's evaluation window, anchor, and entry. Extra history is feature context and does not move that clock.",
        FUTURE_SELECTION_NOTE,
        "Buy-and-hold LONG carries a contract placeholder, not a measured feature.",
        "2024 validation and the 2025 holdout are not inputs to this study.",
        "Actual Binance archive compatibility and licensing remain unresolved.",
    ]
    if calendar.kind is CalendarKind.INJECTED:
        notes.append(
            "This calendar is an injected synthetic fixture. It is not the "
            "production semiannual training study."
        )
    if (
        config.lookback_hours != BASELINE_LOOKBACK_HOURS
        or config.entry_threshold != BASELINE_ENTRY_THRESHOLD
    ):
        notes.append(
            "The caller replaced the frozen 24-hour 2% baseline. This run is a "
            "mechanics check, not a new research default."
        )
    names = tuple(scenario.name for scenario in scenarios)
    if names != tuple(scenario.name for scenario in DEFAULT_SCENARIOS):
        notes.append(
            "The cost scenario list is not the full predetermined three-row grid."
        )
    if any(not fold.coverage.hourly_grid_complete for fold in folds):
        notes.append(
            "At least one fold has an incomplete hourly grid. Sharpe, Sortino, "
            "and volatility stay undefined there."
        )
    if any(fold.coverage.short_sample for fold in folds):
        notes.append(
            "At least one fold has fewer than 24 hourly returns. Annualized "
            "ratios on that fold are not a significance claim."
        )
    if any(cell.fill_count == 0 for cell in cells):
        notes.append(
            "At least one sensitivity cell has zero fills and is still reported."
        )
    return tuple(notes)


def _fold_payload(fold: FoldEvaluation) -> dict[str, object]:
    return {
        "anchor_time": format_utc(fold.anchor_time),
        "entry_time": format_utc(fold.entry_time),
        "evaluation_end": format_utc(fold.fold.evaluation_end),
        "evaluation_start": format_utc(fold.fold.evaluation_start),
        "fold_id": fold.fold_id,
        "history_end": format_utc(fold.fold.history_end),
        "history_start": format_utc(fold.fold.history_start),
        "hourly_grid_complete": fold.coverage.hourly_grid_complete,
        "market_gap_count": fold.market_gap_count,
        "name": fold.fold.name,
        "scenarios": [_scenario_payload(scenario) for scenario in fold.scenarios],
    }


def _scenario_payload(scenario: FoldScenarioResult) -> dict[str, object]:
    return {
        "fee_rate": format_decimal(scenario.scenario.fee_rate),
        "name": scenario.scenario.name,
        "policies": [_policy_payload(policy) for policy in scenario.policies],
        "slippage_rate": format_decimal(scenario.scenario.slippage_rate),
    }


def _policy_payload(policy: PolicyEvaluation) -> dict[str, object]:
    result = policy.result
    return {
        "anchor_btc": format_decimal(policy.anchor_btc),
        "anchor_cash": format_decimal(policy.anchor_cash),
        "exposure_fraction": format_decimal(result.exposure_fraction)
        if result.exposure_fraction is not None
        else None,
        "fill_count": result.fill_count,
        "final_btc": format_decimal(policy.final_btc),
        "final_cash": format_decimal(policy.final_cash),
        "metrics": [_metric_payload(metric) for metric in result.metrics],
        "name": result.name,
        "posted_fees": format_decimal(result.posted_fees),
        "run_id": result.run_id,
        "signal_role": result.signal_role,
        "stale_count": result.stale_count,
        "strategy_config_id": result.strategy_config_id,
    }


def _cell_payload(cell: SensitivityCell) -> dict[str, object]:
    return {
        "cell_id": cell.cell_id,
        "entry_threshold": format_decimal(cell.entry_threshold),
        "fill_count": cell.fill_count,
        "anchor_time": format_utc(cell.anchor_time),
        "entry_time": format_utc(cell.entry_time),
        "fold_name": cell.fold_name,
        "hourly_grid_complete": cell.hourly_grid_complete,
        "lookback_hours": cell.lookback_hours,
        "market_gap_count": cell.market_gap_count,
        "metrics": [_metric_payload(metric) for metric in cell.metrics],
        "posted_fees": format_decimal(cell.posted_fees),
        "run_id": cell.run_id,
        "scenario": cell.scenario.name,
        "strategy_config_id": cell.strategy_config_id,
    }


def _metric_payload(metric: MetricResult) -> dict[str, object]:
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


def _policy_lines(policy: dict[str, object]) -> list[str]:
    lines = [
        f"##### {policy['name']}",
        "",
        f"- Run ID: `{policy['run_id']}`",
        f"- Signal role: `{policy['signal_role']}`",
        f"- Fills: {policy['fill_count']}",
        f"- Posted fees: {policy['posted_fees']}",
        f"- Anchor cash: {policy['anchor_cash']}",
        f"- Anchor BTC: {policy['anchor_btc']}",
        f"- Final cash: {policy['final_cash']}",
        f"- Final BTC: {policy['final_btc']}",
        "",
    ]
    metrics = policy["metrics"]
    assert isinstance(metrics, list)
    for metric in metrics:
        assert isinstance(metric, dict)
        lines.append(f"- {_metric_line(metric)}")
    lines.append("")
    return lines


def _metric_line(metric: dict[str, object]) -> str:
    shown = metric["value"]
    if shown is None and metric["value_hex"] is not None:
        shown = float.fromhex(str(metric["value_hex"])).hex()
    reason = metric["reason"] if metric["reason"] is not None else "defined"
    return (
        f"{metric['name']}: {shown} ({reason}; {metric['coverage']}; "
        f"n={metric['observation_count']})"
    )
