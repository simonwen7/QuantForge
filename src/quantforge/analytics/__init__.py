"""Synthetic risk analytics and the M7 research report.

These figures describe a simulation. They are not BTCUSDT performance evidence.
"""

from quantforge.analytics.models import (
    ANALYTICS_VERSION,
    DEFAULT_SCENARIOS,
    SYNTHETIC_STATEMENT,
    CostScenario,
    ResearchReport,
)
from quantforge.analytics.report import (
    demonstration_bars,
    render_markdown,
    report_payload,
    run_synthetic_study,
    write_synthetic_demonstration,
)

__all__ = [
    "ANALYTICS_VERSION",
    "DEFAULT_SCENARIOS",
    "SYNTHETIC_STATEMENT",
    "CostScenario",
    "ResearchReport",
    "demonstration_bars",
    "render_markdown",
    "report_payload",
    "run_synthetic_study",
    "write_synthetic_demonstration",
]
