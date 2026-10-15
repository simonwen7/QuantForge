"""Write the offline synthetic demonstration report.

Run ``python -m quantforge.analytics`` from an environment that already has the
project installed. The command does not download market data.
"""

from __future__ import annotations

import sys
from pathlib import Path

from quantforge.analytics.report import write_synthetic_demonstration


def main(argv: list[str] | None = None) -> None:
    """Write JSON and Markdown for the fixed synthetic fixture."""
    arguments = list(sys.argv if argv is None else argv)
    destination = Path(arguments[1] if len(arguments) > 1 else "artifacts/m7-synthetic")
    report = write_synthetic_demonstration(destination)
    print(f"{report.report_id} {destination}")


if __name__ == "__main__":
    main()
