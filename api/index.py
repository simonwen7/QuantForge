"""Vercel Python entry. The function does not use the developer virtualenv."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from quantforge.api.app import create_app

app = create_app()
