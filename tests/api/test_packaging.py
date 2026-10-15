"""Deployment files the API project needs before a hosted run exists."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_requirements_pin_the_api_runtime():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "fastapi==0.143.1" in text
    assert "duckdb==1.5.6" in text
    assert "pydantic==2.14.1" in text
    assert "pytest" not in text


def test_vercel_function_points_at_the_python_entry():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["installCommand"] == "pip install -r requirements.txt"
    assert "api/index.py" in config["functions"]
    ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8")
    assert "pyproject.toml" in ignored
    assert "uv.lock" in ignored
    assert "src" not in {line.strip() for line in ignored.splitlines()}


def test_index_module_exports_the_asgi_app():
    path = ROOT / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("quantforge_vercel_entry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app.title == "QuantForge Terminal"
