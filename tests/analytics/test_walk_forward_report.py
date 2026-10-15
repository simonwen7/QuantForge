"""Synthetic fixed-rule walk-forward mechanics. These are not market results."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantforge.analytics.models import DEFAULT_SCENARIOS, CostScenario
from quantforge.analytics.walk_forward_report import (
    WALK_FORWARD_STATEMENT,
    _study_identity,
    baseline_outcome_count,
    injected_mechanics_bars,
    injected_mechanics_calendar,
    render_markdown,
    run_walk_forward_study,
    sensitivity_outcome_count,
    study_payload,
    write_walk_forward_study,
)
from quantforge.backtest.models import run_identity
from quantforge.research.market_bars import DataOrigin, synthetic_market_bar
from quantforge.research.walk_forward import (
    WalkForwardFold,
    injected_calendar,
    production_calendar,
)
from quantforge.strategies.mean_reversion import MeanReversionConfig
from quantforge.strategies.momentum import MomentumConfig

HOUR = timedelta(hours=1)
ORIGIN = datetime(2022, 3, 1, tzinfo=UTC)


def _fold(hours: int = 6, *, origin: datetime = ORIGIN, context: int = 24):
    evaluation_start = origin + context * HOUR
    return WalkForwardFold(
        "fold-1",
        origin,
        evaluation_start,
        evaluation_start,
        evaluation_start + hours * HOUR,
    )


def _bars(
    count: int,
    *,
    origin: datetime = ORIGIN,
    close_at: dict[int, str] | None = None,
    open_at: dict[int, str] | None = None,
    sha: str = "a" * 64,
):
    closes = close_at or {}
    opens = open_at or {}
    return tuple(
        synthetic_market_bar(
            origin + index * HOUR,
            opens.get(index, closes.get(index, "100")),
            close=closes.get(index, "100"),
            archive_sha=sha,
        )
        for index in range(count)
    )


def _one_fold_study(**kwargs):
    calendar = injected_calendar("injected-synthetic-one-fold", (_fold(),))
    return run_walk_forward_study(
        _bars(30),
        calendar,
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
        **kwargs,
    )


def test_production_dimensions_and_short_bars_are_not_a_semiannual_study():
    assert baseline_outcome_count(4) == 48
    assert sensitivity_outcome_count(4) == 108
    with pytest.raises(Exception, match="missing|causal context"):
        run_walk_forward_study(_bars(30), production_calendar())


def test_injected_study_reports_every_outcome_without_selecting_one():
    study = run_walk_forward_study(
        injected_mechanics_bars(),
        injected_mechanics_calendar(),
    )
    payload = study_payload(study)
    markdown = render_markdown(study)
    run_ids = [
        policy["run_id"]
        for fold in payload["folds"]
        for scenario in fold["scenarios"]
        for policy in scenario["policies"]
    ]
    cell_ids = [cell["cell_id"] for cell in payload["sensitivity"]]

    assert study.statement == WALK_FORWARD_STATEMENT
    assert "not a selected configuration" in study.statement
    assert any("not a second confirmation" in warning for warning in study.warnings)
    assert any("does not move that clock" in warning for warning in study.warnings)
    assert len(study.folds) == 4
    assert len(run_ids) == 48
    assert len(set(run_ids)) == 48
    assert len(study.sensitivity) == 108
    assert len(set(cell_ids)) == 108
    assert any(cell.fill_count == 0 for cell in study.sensitivity)
    keys = {
        (cell.fold_name, cell.lookback_hours, cell.entry_threshold, cell.scenario.name)
        for cell in study.sensitivity
    }
    assert len(keys) == 108
    clocks = {
        (cell.fold_name, cell.anchor_time, cell.entry_time)
        for cell in study.sensitivity
    }
    assert len(clocks) == 4
    for fold in study.folds:
        same_fold = [
            cell for cell in study.sensitivity if cell.fold_name == fold.fold.name
        ]
        assert {cell.anchor_time for cell in same_fold} == {fold.anchor_time}
        assert {cell.entry_time for cell in same_fold} == {fold.entry_time}
        counts = {
            next(
                metric.observation_count
                for metric in cell.metrics
                if metric.name == "total_return"
            )
            for cell in same_fold
        }
        assert len(counts) == 1
    for fold in study.folds:
        for scenario in fold.scenarios:
            baseline = next(
                policy
                for policy in scenario.policies
                if policy.result.name == "mean_reversion"
            )
            repeated = next(
                cell
                for cell in study.sensitivity
                if cell.fold_name == fold.fold.name
                and cell.lookback_hours == 24
                and cell.entry_threshold == Decimal("0.02")
                and cell.scenario.name == scenario.scenario.name
            )
            assert repeated.run_id == baseline.result.run_id
    assert "best" not in payload
    assert "stitched" not in payload
    assert "compounded" not in payload
    assert "independent" in markdown
    assert study.study_id in markdown
    assert payload["selection_policy"] == "fixed_rule_no_parameter_selection"
    for fold in study.folds:
        names = [policy.result.name for policy in fold.scenarios[0].policies]
        assert names == ["momentum", "mean_reversion", "cash", "buy_and_hold"]
        clocks = {
            tuple(point_name)
            for point_name in (
                (
                    policy.result.run_id and fold.anchor_time,
                    fold.entry_time,
                )
                for policy in fold.scenarios[0].policies
            )
        }
        assert len(clocks) == 1
        for scenario in fold.scenarios:
            cash = next(
                policy for policy in scenario.policies if policy.result.name == "cash"
            )
            assert cash.result.fill_count == 0
            assert cash.result.posted_fees == Decimal(0)
            assert cash.anchor_btc == Decimal(0)
            assert cash.anchor_cash == Decimal(10000)
            for policy in scenario.policies:
                assert policy.anchor_btc == Decimal(0)
                assert policy.anchor_cash == Decimal(10000)


def test_repeated_runs_match_and_inputs_change_identity(tmp_path):
    first = _one_fold_study()
    second = _one_fold_study()
    write_walk_forward_study(tmp_path, first)
    write_walk_forward_study(tmp_path / "repeat", first)
    text = (tmp_path / "walk-forward-study.md").read_text()
    blob = (tmp_path / "walk-forward-study.json").read_text()
    assert blob == (tmp_path / "repeat" / "walk-forward-study.json").read_text()
    assert text == (tmp_path / "repeat" / "walk-forward-study.md").read_text()

    assert first.study_id == second.study_id
    assert first.folds[0].fold_id == second.folds[0].fold_id
    assert first.study_id in text
    assert first.study_id in blob
    assert render_markdown(first) == text

    close_changed = run_walk_forward_study(
        _bars(30, close_at={3: "101"}),
        injected_calendar("injected-synthetic-one-fold", (_fold(),)),
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    open_changed = run_walk_forward_study(
        _bars(30, open_at={26: "101"}),
        injected_calendar("injected-synthetic-one-fold", (_fold(),)),
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    source_changed = run_walk_forward_study(
        _bars(30, sha="b" * 64),
        injected_calendar("injected-synthetic-one-fold", (_fold(),)),
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    threshold_changed = _one_fold_study(
        config=MeanReversionConfig(entry_threshold=Decimal("0.05"))
    )
    lookback_changed = _one_fold_study(config=MeanReversionConfig(lookback_hours=12))
    slippage_changed = run_walk_forward_study(
        _bars(30),
        injected_calendar("injected-synthetic-one-fold", (_fold(),)),
        include_sensitivity=False,
        scenarios=(CostScenario("slip", Decimal(0), Decimal("0.0005")),),
    )
    fee_changed = run_walk_forward_study(
        _bars(30),
        injected_calendar("injected-synthetic-one-fold", (_fold(),)),
        include_sensitivity=False,
        scenarios=(CostScenario("custom", Decimal("0.003"), Decimal("0.0005")),),
    )
    longer = injected_calendar(
        "injected-synthetic-longer",
        (_fold(hours=7),),
    )
    boundary_changed = run_walk_forward_study(
        _bars(31),
        longer,
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    originals = {first.study_id, first.folds[0].fold_id}
    assert close_changed.folds[0].fold_id not in originals
    assert open_changed.folds[0].fold_id != first.folds[0].fold_id
    assert source_changed.folds[0].fold_id != first.folds[0].fold_id
    assert threshold_changed.study_id != first.study_id
    assert lookback_changed.study_id != first.study_id
    assert slippage_changed.study_id != first.study_id
    assert fee_changed.study_id != first.study_id
    identity = _study_identity(
        first.calendar,
        first.baseline_config,
        MomentumConfig(),
        first.folds,
        first.sensitivity,
    )
    revised = dict(identity)
    revised["analytics_version"] = "m8-walk-forward-v2"
    revised_grid = dict(identity)
    revised_grid["sensitivity_grid"] = {"lookbacks": [12], "thresholds": ["0.02"]}
    assert run_identity(identity) == first.study_id
    assert run_identity(revised) != first.study_id
    assert run_identity(revised_grid) != first.study_id
    assert boundary_changed.folds[0].fold_id != first.folds[0].fold_id


def test_a_prefold_dip_cannot_fill_inside_the_next_fold():
    """Lookback 12 can signal before the fold. That order must not fill there."""
    closes = {22: "90"}
    study = run_walk_forward_study(
        _bars(30, close_at=closes, open_at=closes),
        injected_calendar("injected-synthetic-prefold", (_fold(),)),
        config=MeanReversionConfig(lookback_hours=12),
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    mean_reversion = next(
        policy
        for policy in study.folds[0].scenarios[0].policies
        if policy.result.name == "mean_reversion"
    )
    fold = study.folds[0]
    assert mean_reversion.anchor_btc == Decimal(0)
    assert mean_reversion.anchor_cash == Decimal(10000)
    assert mean_reversion.result.fill_count == 0
    assert fold.anchor_time == fold.fold.evaluation_start + HOUR
    assert fold.entry_time == fold.fold.evaluation_start + 2 * HOUR


def test_a_gap_stays_visible_and_a_flat_signal_does_not_delay_buy_and_hold():
    bars = [bar for index, bar in enumerate(_bars(30)) if index != 28]
    gapped = run_walk_forward_study(
        bars,
        injected_calendar("injected-synthetic-gap", (_fold(),)),
        allow_gaps=True,
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[1],),
    )
    sharpe = next(
        metric
        for metric in gapped.folds[0].scenarios[0].policies[1].result.metrics
        if metric.name == "sharpe_ratio"
    )
    assert gapped.folds[0].market_gap_count > 0
    assert sharpe.value is None
    assert sharpe.reason is not None

    flat = _one_fold_study()
    mean_reversion = next(
        policy
        for policy in flat.folds[0].scenarios[0].policies
        if policy.result.name == "mean_reversion"
    )
    hold = next(
        policy
        for policy in flat.folds[0].scenarios[0].policies
        if policy.result.name == "buy_and_hold"
    )
    assert mean_reversion.result.fill_count == 0
    assert hold.result.fill_count == 1
    assert hold.result.signal_role == "adapter_not_observed"
    assert flat.folds[0].entry_time == flat.folds[0].anchor_time + HOUR


def test_folds_reset_and_do_not_inherit_positions_or_holdout_bars():
    origin = ORIGIN
    first = WalkForwardFold(
        "fold-1",
        origin,
        origin + 24 * HOUR,
        origin + 24 * HOUR,
        origin + 30 * HOUR,
    )
    second_start = origin + 30 * HOUR
    second = WalkForwardFold(
        "fold-2",
        origin,
        second_start,
        second_start,
        second_start + 6 * HOUR,
    )
    calendar = injected_calendar("injected-synthetic-reset", (first, second))
    falling = tuple(
        synthetic_market_bar(origin + index * HOUR, Decimal(300 - index))
        for index in range(36)
    )
    study = run_walk_forward_study(
        falling,
        calendar,
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    opening = study.folds[0].scenarios[0].policies
    later = study.folds[1].scenarios[0].policies
    mean_first = next(
        policy for policy in opening if policy.result.name == "mean_reversion"
    )
    mean_second = next(
        policy for policy in later if policy.result.name == "mean_reversion"
    )

    assert mean_first.final_btc > 0
    assert mean_second.anchor_btc == Decimal(0)
    assert mean_second.anchor_cash == Decimal(10000)
    assert study.folds[0].fold_id != study.folds[1].fold_id

    verified = replace(falling[0], origin=DataOrigin.VERIFIED_STORAGE)
    with pytest.raises(Exception, match="verified"):
        run_walk_forward_study(
            (verified, *falling[1:]), calendar, include_sensitivity=False
        )
    holdout = synthetic_market_bar(datetime(2025, 1, 1, tzinfo=UTC), "100")
    with pytest.raises(Exception, match="2025|holdout|validation"):
        run_walk_forward_study((holdout,), calendar, include_sensitivity=False)
    validation = synthetic_market_bar(datetime(2024, 1, 1, tzinfo=UTC), "100")
    with pytest.raises(Exception, match="2024|validation|holdout"):
        run_walk_forward_study((validation,), calendar, include_sensitivity=False)


def test_regimes_exercise_mechanics_without_becoming_evidence():
    """Rising, falling, oscillating, and constant paths are fixtures only."""
    start = ORIGIN

    def path(prices: list[str]):
        return tuple(
            synthetic_market_bar(start + index * HOUR, price, close=price)
            for index, price in enumerate(prices)
        )

    rising = path([str(100 + index) for index in range(30)])
    constant = path(["100"] * 30)
    oscillating = path(["110" if index % 2 == 0 else "90" for index in range(30)])
    calendar = injected_calendar("injected-synthetic-regime", (_fold(),))
    for bars in (rising, constant):
        study = run_walk_forward_study(
            bars,
            calendar,
            include_sensitivity=False,
            scenarios=(DEFAULT_SCENARIOS[0],),
        )
        mean_reversion = next(
            policy
            for policy in study.folds[0].scenarios[0].policies
            if policy.result.name == "mean_reversion"
        )
        assert mean_reversion.result.fill_count == 0
        assert "injected synthetic fixture" in " ".join(study.warnings)
    wave = run_walk_forward_study(
        oscillating,
        calendar,
        include_sensitivity=False,
        scenarios=(DEFAULT_SCENARIOS[0],),
    )
    assert any(
        policy.result.fill_count > 0
        for policy in wave.folds[0].scenarios[0].policies
        if policy.result.name == "mean_reversion"
    )
