"""Backtest configuration, event views, orders, and results.

Financial amounts are ``Decimal``. A bar-open view exposes the open only.
Completion fields are a separate object created when that bar is available.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from enum import IntEnum, StrEnum

from quantforge.research.experiment import canonical_json
from quantforge.research.market_bars import DataOrigin, MarketBar
from quantforge.research.models import ResearchError

ENGINE_VERSION = "m6-backtest-v1"
NO_SIGNAL_POLICY = "hold_and_cancel_pending"
VALUATION_CONVENTION = "asof_open_else_completed_close"
ACCOUNTING_PRECISION = 50
LATENCY_BARS = 1
DEFAULT_INITIAL_CASH = Decimal(10000)
DEFAULT_FEE_RATE = Decimal("0.001")
DEFAULT_SLIPPAGE_RATE = Decimal("0.0005")
DEFAULT_QUANTUM = Decimal("0.00000001")


class BacktestError(ResearchError):
    """Base class for expected backtest failures."""


class BacktestDataError(BacktestError):
    """Bars or signals cannot be replayed."""


class BacktestConfigurationError(BacktestError):
    """Execution assumptions violate the M6 contract."""


class BacktestIntegrityError(BacktestError):
    """An accounting or order invariant failed."""


class EventPhase(IntEnum):
    """Deterministic phase order for events that share a timestamp."""

    BAR_OPEN = 1
    FILL_DUE = 2
    BAR_COMPLETE = 3
    SIGNAL = 4
    ORDER_SUBMISSION = 5
    VALUATION = 6


class OrderSide(StrEnum):
    """Spot market-order direction."""

    BUY = "buy"
    SELL = "sell"


class OrderState(StrEnum):
    """Lifecycle of one market order."""

    SUBMITTED = "submitted"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELED = "canceled"


class PriceKind(StrEnum):
    """Which observable print an equity mark used."""

    OPEN = "open"
    CLOSE = "close"


class RejectionReason(StrEnum):
    """Why an order did not change the portfolio."""

    INSUFFICIENT_CASH = "insufficient_cash"
    ZERO_QUANTITY = "zero_quantity"
    INSUFFICIENT_POSITION = "insufficient_position"
    MISSING_EXECUTION_BAR = "missing_execution_bar"
    DUPLICATE_ORDER = "duplicate_order"
    INVALID_SIGNAL = "invalid_signal"
    OUTSIDE_EVALUATION_WINDOW = "outside_evaluation_window"
    NO_EXECUTION_INTENT = "no_execution_intent"


def _decimal(value: Decimal, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise BacktestConfigurationError(f"{label} must be a Decimal")
    if not value.is_finite():
        raise BacktestConfigurationError(f"{label} must be finite")
    return value


def on_cash_quantum(amount: Decimal, quantum: Decimal) -> bool:
    """Return whether ``amount`` is an integer number of cash quanta."""
    with localcontext(Context(prec=ACCOUNTING_PRECISION)):
        units = (amount / quantum).to_integral_value(rounding=ROUND_DOWN)
        return units * quantum == amount


@dataclass(frozen=True)
class ExecutionAssumptions:
    """Synthetic execution assumptions. These are not a venue fee schedule."""

    initial_cash: Decimal = DEFAULT_INITIAL_CASH
    fee_rate: Decimal = DEFAULT_FEE_RATE
    slippage_rate: Decimal = DEFAULT_SLIPPAGE_RATE
    quantity_quantum: Decimal = DEFAULT_QUANTUM
    cash_quantum: Decimal = DEFAULT_QUANTUM
    latency_bars: int = LATENCY_BARS

    def __post_init__(self) -> None:
        cash = _decimal(self.initial_cash, "initial_cash")
        fee = _decimal(self.fee_rate, "fee_rate")
        slip = _decimal(self.slippage_rate, "slippage_rate")
        quantity = _decimal(self.quantity_quantum, "quantity_quantum")
        cash_quantum = _decimal(self.cash_quantum, "cash_quantum")
        if cash <= 0:
            raise BacktestConfigurationError("initial_cash must be positive")
        if fee < 0 or fee >= 1:
            raise BacktestConfigurationError("fee_rate must be in [0, 1)")
        if slip < 0 or slip >= 1:
            raise BacktestConfigurationError("slippage_rate must be in [0, 1)")
        if quantity <= 0 or cash_quantum <= 0:
            raise BacktestConfigurationError("quantization quanta must be positive")
        if not on_cash_quantum(cash, cash_quantum):
            raise BacktestConfigurationError(
                "initial_cash must be an integer multiple of cash_quantum"
            )
        if isinstance(self.latency_bars, bool) or self.latency_bars != LATENCY_BARS:
            raise BacktestConfigurationError("M6 latency_bars is fixed at 1")


@dataclass(frozen=True)
class EvaluationWindow:
    """Half-open interval of fill timestamps that belong to one run."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is not UTC or self.end.tzinfo is not UTC:
            raise BacktestConfigurationError("evaluation bounds must use datetime.UTC")
        if self.start >= self.end:
            raise BacktestConfigurationError("evaluation window must be nonempty")

    def contains(self, timestamp: datetime) -> bool:
        """Return whether an execution timestamp belongs to this window."""
        return self.start <= timestamp < self.end


@dataclass(frozen=True)
class BarOpen:
    """The only market fields observable when a bar opens."""

    open_time: datetime
    open: Decimal
    source_archive_sha256: str
    source_year: int
    source_month: int
    source_schema_version: str


@dataclass(frozen=True)
class BarComplete:
    """OHLCV fields observable when a bar completes, one hour after it opens."""

    open_time: datetime
    close_time: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_archive_sha256: str
    source_year: int
    source_month: int
    source_schema_version: str


def bar_open_view(bar: MarketBar) -> BarOpen:
    """Copy the open fields without the bar's completion fields."""
    return BarOpen(
        open_time=bar.open_time,
        open=bar.open,
        source_archive_sha256=bar.source_archive_sha256,
        source_year=bar.source_year,
        source_month=bar.source_month,
        source_schema_version=bar.source_schema_version,
    )


def bar_complete_view(bar: MarketBar) -> BarComplete:
    """Copy the full bar once its ``available_at`` has been reached."""
    return BarComplete(
        open_time=bar.open_time,
        close_time=bar.close_time,
        available_at=bar.available_at,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        source_archive_sha256=bar.source_archive_sha256,
        source_year=bar.source_year,
        source_month=bar.source_month,
        source_schema_version=bar.source_schema_version,
    )


@dataclass(frozen=True)
class Order:
    """One deterministic market order. Identifiers do not use the wall clock."""

    order_id: str
    state: OrderState
    side: OrderSide
    signal_open_time: datetime
    submitted_at: datetime
    due_time: datetime
    reason: RejectionReason | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class Fill:
    """A booked execution. Later events must not mutate this record."""

    fill_id: str
    order_id: str
    time: datetime
    side: OrderSide
    quantity: Decimal
    raw_open: Decimal
    execution_price: Decimal
    posted_notional: Decimal
    posted_fee: Decimal
    posted_cash_delta: Decimal
    rounding_residual: Decimal
    source_archive_sha256: str
    source_year: int
    source_month: int
    source_schema_version: str
    signal_open_time: datetime


@dataclass(frozen=True)
class LedgerEntry:
    """The cash and position movement posted for one fill."""

    entry_id: str
    fill_id: str
    time: datetime
    cash_delta: Decimal
    btc_delta: Decimal
    fee: Decimal
    realized_delta: Decimal
    cash_after: Decimal
    btc_after: Decimal


@dataclass(frozen=True)
class EquityPoint:
    """A fresh mark at the latest price observable at ``time``.

    When a bar opens at ``time``, the mark uses that open. A completed close is
    used only when no open is observable at the same timestamp. Missing hours
    are not interpolated.
    """

    time: datetime
    price: Decimal
    price_kind: PriceKind
    price_open_time: datetime
    cash: Decimal
    btc: Decimal
    equity: Decimal
    fresh: bool
    in_evaluation: bool


@dataclass(frozen=True)
class StaleValuation:
    """A held position whose last price is older than the current gap."""

    time: datetime
    last_price: Decimal
    last_price_time: datetime
    cash: Decimal
    btc: Decimal
    fresh: bool = False


@dataclass(frozen=True)
class MarketGap:
    """Two successive observed opens that are not one hour apart."""

    previous_open_time: datetime
    next_open_time: datetime


@dataclass(frozen=True)
class EngineEvent:
    """One replay event. Completion views are absent from open events."""

    time: datetime
    phase: EventPhase
    name: str
    open_view: BarOpen | None = None
    complete_view: BarComplete | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class BacktestResult:
    """Deterministic ledger, marks, and the run identifier."""

    run_id: str
    data_origin: DataOrigin
    engine_version: str
    strategy_config_id: str
    assumptions: ExecutionAssumptions
    events: tuple[EngineEvent, ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    ledger: tuple[LedgerEntry, ...]
    equity_points: tuple[EquityPoint, ...]
    stale_valuations: tuple[StaleValuation, ...]
    market_gaps: tuple[MarketGap, ...]
    final_cash: Decimal
    final_btc: Decimal
    realized_pnl: Decimal
    accumulated_fees: Decimal
    no_signal_policy: str = NO_SIGNAL_POLICY


def format_utc(value: datetime) -> str:
    """Serialize a UTC timestamp to microsecond precision."""
    if value.tzinfo is not UTC:
        raise BacktestDataError("backtest timestamps must use datetime.UTC")
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def format_decimal(value: Decimal) -> str:
    """Serialize a Decimal without binary floating point."""
    return format(value, "f")


def order_identity(
    *,
    strategy_config_id: str,
    signal_open_time: datetime,
    side: OrderSide,
    due_time: datetime,
) -> str:
    """Hash the economic identity of an order."""
    payload = {
        "due_time": format_utc(due_time),
        "side": side.value,
        "signal_open_time": format_utc(signal_open_time),
        "strategy_config_id": strategy_config_id,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def fill_identity(order_id: str) -> str:
    """Derive the single fill identifier allowed for an order."""
    return hashlib.sha256(f"{order_id}:fill".encode()).hexdigest()


def due_time_for(available_at: datetime) -> datetime:
    """Return the latency-one execution open."""
    return available_at + timedelta(hours=LATENCY_BARS)


def run_identity(payload: dict[str, object]) -> str:
    """Hash a backtest identity document. Wall-clock time is not an input."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
