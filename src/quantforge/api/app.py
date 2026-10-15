"""Terminal HTTP API. Financial work stays in the session adapter."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from quantforge.api.serialize import compare_payload, config_payload, session_payload
from quantforge.api.session import (
    SessionError,
    blocked_research_payload,
    run_backtest,
    run_compare,
)
from quantforge.research.models import ResearchError
from quantforge.synthetic.scenarios import (
    COMPARE_MAX_BARS,
    MAX_BARS,
    MAX_LOOKBACK,
    MAX_SEED,
    MIN_BARS,
)

_BODY_LIMIT = 8192


class BacktestRequest(BaseModel):
    """One synthetic session. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    strategy: str
    lookback_hours: int = Field(ge=1, le=MAX_LOOKBACK)
    scenario: str
    seed: int = Field(ge=0, le=MAX_SEED)
    bar_count: int = Field(ge=MIN_BARS, le=MAX_BARS)
    cost: str
    entry_threshold: str | None = None


class CompareRequest(BaseModel):
    """Both rules on one path. A strategy winner is not selected."""

    model_config = ConfigDict(extra="forbid")

    lookback_hours: int = Field(ge=1, le=MAX_LOOKBACK)
    scenario: str
    seed: int = Field(ge=0, le=MAX_SEED)
    bar_count: int = Field(ge=MIN_BARS, le=COMPARE_MAX_BARS)
    entry_threshold: str | None = None


def create_app() -> FastAPI:
    """Build the ASGI app used locally and on Vercel."""
    app = FastAPI(title="QuantForge Terminal", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        raw = request.headers.get("content-length")
        if raw is not None and raw.isdigit() and int(raw) > _BODY_LIMIT:
            return JSONResponse(
                status_code=413,
                content={"detail": "request body exceeds 8 KB"},
            )
        return await call_next(request)

    @app.exception_handler(ResearchError)
    async def research_error(_request: Request, exc: ResearchError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "quantforge-terminal"}

    @app.get("/api/config")
    @app.get("/config")
    def config() -> dict[str, object]:
        return config_payload()

    @app.post("/api/backtest")
    @app.post("/backtest")
    def backtest(body: BacktestRequest) -> dict[str, object]:
        session = run_backtest(
            strategy=body.strategy,
            lookback_hours=body.lookback_hours,
            scenario=body.scenario,
            seed=body.seed,
            bar_count=body.bar_count,
            cost=body.cost,
            entry_threshold=_decimal(body.entry_threshold),
        )
        return session_payload(session)

    @app.post("/api/compare")
    @app.post("/compare")
    def compare(body: CompareRequest) -> dict[str, object]:
        session = run_compare(
            lookback_hours=body.lookback_hours,
            entry_threshold=_decimal(body.entry_threshold),
            scenario=body.scenario,
            seed=body.seed,
            bar_count=body.bar_count,
        )
        return compare_payload(session)

    @app.get("/api/research-block")
    @app.get("/research-block")
    def research_block(request: Request) -> dict[str, object]:
        if request.query_params:
            raise SessionError("research-block does not accept parameters")
        return blocked_research_payload()

    return app


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise SessionError("entry_threshold is not a decimal") from error
