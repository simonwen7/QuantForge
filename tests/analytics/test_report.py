"""Deterministic synthetic report identity and holdout rejection."""

import json
import os
import subprocess
import sys
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


def test_cli_keeps_the_default_directory_and_isolates_an_explicit_one(
    tmp_path, monkeypatch
):
    def explode(*args, **kwargs):
        raise AssertionError("network access")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    monkeypatch.chdir(tmp_path)
    from quantforge.analytics.__main__ import main

    explicit = tmp_path / "chosen"
    main(["quantforge.analytics", os.fspath(explicit)])
    assert not (tmp_path / "artifacts").exists()
    first_json = (explicit / "synthetic-study.json").read_bytes()
    first_markdown = (explicit / "synthetic-study.md").read_bytes()
    assert first_json and first_markdown
    payload = json.loads(first_json)
    assert payload["statement"].startswith("SYNTHETIC MECHANICS DEMONSTRATION")
    assert "not actual BTCUSDT performance evidence" in payload["statement"]
    assert "profitab" not in payload["statement"].lower()

    main(["quantforge.analytics", os.fspath(explicit)])
    assert (explicit / "synthetic-study.json").read_bytes() == first_json
    assert (explicit / "synthetic-study.md").read_bytes() == first_markdown

    main(["quantforge.analytics"])
    default_dir = tmp_path / "artifacts" / "m7-synthetic"
    assert (default_dir / "synthetic-study.json").read_bytes() == first_json
    assert (default_dir / "synthetic-study.md").read_bytes() == first_markdown
    assert json.loads(first_json)["report_id"] == payload["report_id"]


def test_module_entrypoint_writes_only_the_requested_directory(tmp_path):
    destination = tmp_path / "outside"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-m", "quantforge.analytics", os.fspath(destination)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    first_json = (destination / "synthetic-study.json").read_bytes()
    first_markdown = (destination / "synthetic-study.md").read_bytes()
    payload = json.loads(first_json)
    assert completed.stdout.split()[0] == payload["report_id"]
    assert first_markdown
    assert not (tmp_path / "artifacts").exists()
    repeat = subprocess.run(
        [sys.executable, "-m", "quantforge.analytics", os.fspath(destination)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert repeat.stdout.split()[0] == payload["report_id"]
    assert (destination / "synthetic-study.json").read_bytes() == first_json
    assert (destination / "synthetic-study.md").read_bytes() == first_markdown
