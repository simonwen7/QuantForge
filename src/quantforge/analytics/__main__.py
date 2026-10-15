"""Write the offline synthetic demonstration report.

``python -m quantforge.analytics`` writes ``artifacts/m7-synthetic/``.
``python -m quantforge.analytics <directory>`` writes into that directory.
Neither form downloads market data.
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
