"""Decimal cash, BTC, fee, and cost-basis accounting.

Buy quantity is rounded down. The posted buy debit is rounded up to the cash
quantum and is the amount removed from cash and added to cost. Sell proceeds
are rounded down after the fee is rounded up. A zero or negative posted credit
is not a fill. Cost and realized results use those posted amounts.

``rounding_residual`` is the gap between the exact product and the posted cash
amount. It is strictly less than one cash quantum. It is not an off-grid cash
balance. Starting cash and every posted debit or credit are integer multiples
of the cash quantum, so the cash balance remains on that grid. Uninvested cash
after an all-in buy is also on that grid; it can exceed one quantum because one
BTC quantum can cost more than one cash quantum.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, localcontext

from quantforge.backtest.models import (
    ACCOUNTING_PRECISION,
    DEFAULT_QUANTUM,
    BacktestIntegrityError,
    ExecutionAssumptions,
    Fill,
    LedgerEntry,
    OrderSide,
    on_cash_quantum,
)
from quantforge.research.returns import return_context

_CASH = "cash"


def accounting_context():
    """Return the precision-50 context shared with research returns."""
    context = return_context()
    if context.prec != ACCOUNTING_PRECISION:
        raise BacktestIntegrityError(
            "accounting precision drifted from the research context"
        )
    return context


def _quantize(amount: Decimal, quantum: Decimal, rounding: str) -> Decimal:
    with localcontext(accounting_context()):
        units = (amount / quantum).to_integral_value(rounding=rounding)
        return units * quantum


def execution_price(
    raw_open: Decimal, assumptions: ExecutionAssumptions, side: OrderSide
) -> Decimal:
    """Apply adverse slippage to an observed open."""
    with localcontext(accounting_context()):
        if side is OrderSide.BUY:
            return raw_open * (Decimal(1) + assumptions.slippage_rate)
        return raw_open * (Decimal(1) - assumptions.slippage_rate)


@dataclass(frozen=True)
class PostedBuy:
    """A buy whose cash debit has already been quantized."""

    quantity: Decimal
    raw_open: Decimal
    execution_price: Decimal
    posted_notional: Decimal
    posted_fee: Decimal
    posted_debit: Decimal
    rounding_residual: Decimal


@dataclass(frozen=True)
class PostedSell:
    """A full-position sell whose cash credit has already been quantized."""

    quantity: Decimal
    raw_open: Decimal
    execution_price: Decimal
    posted_notional: Decimal
    posted_fee: Decimal
    posted_proceeds: Decimal
    rounding_residual: Decimal


def quote_buy(
    quantity: Decimal,
    raw_open: Decimal,
    assumptions: ExecutionAssumptions,
) -> PostedBuy | None:
    """Price an explicit buy. ``None`` means the posted debit is not positive."""
    if quantity <= 0:
        return None
    price = execution_price(raw_open, assumptions, OrderSide.BUY)
    with localcontext(accounting_context()):
        raw_notional = quantity * price
        raw_fee = raw_notional * assumptions.fee_rate
        raw_debit = raw_notional + raw_fee
    posted_debit = _quantize(raw_debit, assumptions.cash_quantum, ROUND_CEILING)
    posted_fee = _quantize(raw_fee, assumptions.cash_quantum, ROUND_CEILING)
    if posted_debit <= 0 or posted_fee > posted_debit:
        return None
    posted_notional = posted_debit - posted_fee
    residual = posted_debit - raw_debit
    if posted_notional + posted_fee != posted_debit:
        raise BacktestIntegrityError("posted buy parts do not sum to the debit")
    if residual < 0 or residual >= assumptions.cash_quantum:
        raise BacktestIntegrityError(
            "buy rounding residual is outside one cash quantum"
        )
    return PostedBuy(
        quantity=quantity,
        raw_open=raw_open,
        execution_price=price,
        posted_notional=posted_notional,
        posted_fee=posted_fee,
        posted_debit=posted_debit,
        rounding_residual=residual,
    )


def plan_all_in_buy(
    cash: Decimal,
    raw_open: Decimal,
    assumptions: ExecutionAssumptions,
) -> PostedBuy | None:
    """Buy the largest quantized quantity whose posted debit fits in cash."""
    price = execution_price(raw_open, assumptions, OrderSide.BUY)
    with localcontext(accounting_context()):
        raw_quantity = cash / (price * (Decimal(1) + assumptions.fee_rate))
    quantity = _quantize(raw_quantity, assumptions.quantity_quantum, ROUND_DOWN)
    while quantity > 0:
        quote = quote_buy(quantity, raw_open, assumptions)
        if quote is not None and quote.posted_debit <= cash:
            return quote
        quantity -= assumptions.quantity_quantum
    return None


def quote_sell(
    quantity: Decimal,
    raw_open: Decimal,
    assumptions: ExecutionAssumptions,
) -> PostedSell | None:
    """Price a sell. Proceeds are what cash actually receives."""
    if quantity <= 0:
        return None
    price = execution_price(raw_open, assumptions, OrderSide.SELL)
    if price <= 0:
        return None
    with localcontext(accounting_context()):
        raw_notional = quantity * price
        raw_fee = raw_notional * assumptions.fee_rate
    posted_fee = _quantize(raw_fee, assumptions.cash_quantum, ROUND_CEILING)
    if posted_fee > raw_notional:
        return None
    with localcontext(accounting_context()):
        after_fee = raw_notional - posted_fee
    posted_proceeds = _quantize(after_fee, assumptions.cash_quantum, ROUND_DOWN)
    if posted_proceeds <= 0:
        return None
    residual = after_fee - posted_proceeds
    if residual < 0 or residual >= assumptions.cash_quantum:
        raise BacktestIntegrityError(
            "sell rounding residual is outside one cash quantum"
        )
    if posted_proceeds + posted_fee + residual != raw_notional:
        raise BacktestIntegrityError("posted sell parts do not sum to the raw notional")
    return PostedSell(
        quantity=quantity,
        raw_open=raw_open,
        execution_price=price,
        posted_notional=raw_notional,
        posted_fee=posted_fee,
        posted_proceeds=posted_proceeds,
        rounding_residual=residual,
    )


@dataclass
class Portfolio:
    """Mutable book used only inside one run. Results copy frozen records."""

    cash: Decimal
    btc: Decimal = Decimal(0)
    cost_total: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    cash_quantum: Decimal = DEFAULT_QUANTUM
    fills: list[Fill] | None = None
    ledger: list[LedgerEntry] | None = None

    def __post_init__(self) -> None:
        if self.fills is None:
            self.fills = []
        if self.ledger is None:
            self.ledger = []
        self._fill_ids: set[str] = set()
        self._assert_nonnegative()

    def apply_buy(self, quote: PostedBuy, fill: Fill, entry: LedgerEntry) -> None:
        """Post one buy. A repeated fill identifier is an integrity failure."""
        self._reserve(fill.fill_id)
        if quote.posted_debit > self.cash:
            raise BacktestIntegrityError("buy debit exceeds cash")
        if fill.posted_cash_delta != -quote.posted_debit:
            raise BacktestIntegrityError("buy ledger does not match the posted debit")
        self.cash -= quote.posted_debit
        self.btc += quote.quantity
        self.cost_total += quote.posted_debit
        self.fees += quote.posted_fee
        self._record(fill, entry)

    def apply_sell(self, quote: PostedSell, fill: Fill, entry: LedgerEntry) -> None:
        """Liquidate the posted quantity and realize against the capitalized cost."""
        self._reserve(fill.fill_id)
        if quote.quantity != self.btc:
            raise BacktestIntegrityError("M6 sells the full BTC position")
        if fill.posted_cash_delta != quote.posted_proceeds:
            raise BacktestIntegrityError(
                "sell ledger does not match the posted proceeds"
            )
        cost_removed = self.cost_total
        realized_delta = quote.posted_proceeds - cost_removed
        self.cash += quote.posted_proceeds
        self.realized_pnl += realized_delta
        self.fees += quote.posted_fee
        self.btc = Decimal(0)
        self.cost_total = Decimal(0)
        if entry.realized_delta != realized_delta:
            raise BacktestIntegrityError("realized ledger does not match cost removal")
        self._record(fill, entry)

    def equity(self, price: Decimal) -> Decimal:
        """Mark the book at one available price."""
        with localcontext(accounting_context()):
            return self.cash + self.btc * price

    def _reserve(self, fill_id: str) -> None:
        if fill_id in self._fill_ids:
            raise BacktestIntegrityError("fill was already posted")

    def _record(self, fill: Fill, entry: LedgerEntry) -> None:
        if entry.fill_id != fill.fill_id:
            raise BacktestIntegrityError("ledger entry is not tied to its fill")
        if entry.cash_after != self.cash or entry.btc_after != self.btc:
            raise BacktestIntegrityError("ledger balances do not match the portfolio")
        self._fill_ids.add(fill.fill_id)
        assert self.fills is not None
        assert self.ledger is not None
        self.fills.append(fill)
        self.ledger.append(entry)
        self._assert_nonnegative()

    def _assert_nonnegative(self) -> None:
        if self.cash < 0 or self.btc < 0:
            raise BacktestIntegrityError("cash and BTC must remain nonnegative")
        if self.btc == 0 and self.cost_total != 0:
            raise BacktestIntegrityError("flat book still carries acquisition cost")
        if self.btc > 0 and self.cost_total <= 0:
            raise BacktestIntegrityError("open BTC is missing capitalized cost")
        if not on_cash_quantum(self.cash, self.cash_quantum):
            raise BacktestIntegrityError("cash is not a multiple of the cash quantum")
