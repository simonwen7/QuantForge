"""Deterministic synthetic report identity and holdout rejection."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quantforge.analytics import report as report_module
from quantforge.analytics.models import AnalyticsError, CostScenario
from quantforge.analytics.report import (
    render_markdown,
    report_payload,
    run_synthetic_study,
    write_synthetic_demonstration,
)
from quantforge.backtest.models import run_identity
from quantforge.research import synthetic_market_bar
from quantforge.research.experiment import canonical_json
from quantforge.strategies import MomentumConfig

HOUR = timedelta(hours=1)
START = datetime(2022, 6, 1, tzinfo=UTC)


def _short_bars(last_close: str = "80"):
    rows = []
    for index in range(8):
        price = Decimal(100 + index) if index < 7 else Decimal(last_close)
        rows.append(synthetic_market_bar(START + index * HOUR, price, close=price))
    return tuple(rows)


def test_repeated_studies_match_and_inputs_change_the_report_id(tmp_path):
    config = MomentumConfig(lookback_hours=1)
    bars = _short_bars()
    first = run_synthetic_study(bars, config=config)
    second = run_synthetic_study(bars, config=config)
    changed_bar = run_synthetic_study(_short_bars("81"), config=config)
    changed_config = run_synthetic_study(
        bars,
        config=MomentumConfig(lookback_hours=2),
    )
    zero_only = run_synthetic_study(
        bars,
        config=config,
        scenarios=(CostScenario("zero", Decimal(0), Decimal(0)),),
    )
    payload = report_payload(first)
    rendered = render_markdown(first)

    assert first.report_id == second.report_id
    assert canonical_json(payload) == canonical_json(report_payload(second))
    assert rendered == render_markdown(second)
    assert first.report_id in rendered
    assert "SYNTHETIC MECHANICS DEMONSTRATION" in rendered
    assert changed_bar.report_id != first.report_id
    assert changed_config.report_id != first.report_id
    assert zero_only.report_id != first.report_id
    identity = report_module._identity_payload(
        config,
        Decimal(10000),
        first.coverage,
        first.scenarios,
    )
    revised = dict(identity)
    revised["analytics_version"] = "m7-analytics-v2"
    revised_return = dict(identity)
    revised_return["return_convention"] = "close_to_close"
    assert run_identity(identity) == first.report_id
    assert run_identity(revised) != first.report_id
    assert run_identity(revised_return) != first.report_id
    assert [item.scenario.name for item in first.scenarios] == [
        "zero",
        "baseline",
        "pessimistic_2x",
    ]
    names = {
        policy.name for scenario in first.scenarios for policy in scenario.policies
    }
    assert names == {"momentum", "cash", "buy_and_hold"}
    written = write_synthetic_demonstration(tmp_path)
    text = (tmp_path / "synthetic-study.json").read_text(encoding="utf-8")
    markdown = (tmp_path / "synthetic-study.md").read_text(encoding="utf-8")
    assert written.report_id in text
    assert written.report_id in markdown
    assert "BTCUSDT performance evidence" in markdown


def test_verified_and_2025_bars_are_rejected_and_the_network_is_unused(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    bars = _short_bars()
    holdout = synthetic_market_bar(datetime(2025, 1, 1, tzinfo=UTC), "100")
    verified = synthetic_market_bar(START, "100")
    object.__setattr__(verified, "origin", "verified_storage")

    with pytest.raises(AnalyticsError, match="2025"):
        run_synthetic_study((holdout, *bars[1:]))
    with pytest.raises(AnalyticsError, match="verified"):
        run_synthetic_study((verified, *bars[1:]))
    run_synthetic_study(bars, config=MomentumConfig(lookback_hours=1))
    with pytest.raises(AnalyticsError, match="directional"):
        run_synthetic_study(bars)
