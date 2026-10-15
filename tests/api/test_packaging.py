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
    assert "installCommand" not in config
    assert config["functions"]["api/index.py"]["maxDuration"] == 30
    ignored_lines = {
        line.strip() for line in (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    }
    assert "pyproject.toml" in ignored_lines
    assert "uv.lock" in ignored_lines
    assert "requirements.txt" not in ignored_lines
    assert "src" not in ignored_lines
    web = ROOT / "apps" / "web"
    assert (web / "package.json").is_file()
    assert (web / "package-lock.json").is_file()
    assert not (web / "vercel.json").exists()


def test_index_module_exports_the_asgi_app():
    path = ROOT / "api" / "index.py"
    spec = importlib.util.spec_from_file_location("quantforge_vercel_entry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.app.title == "QuantForge Terminal"
