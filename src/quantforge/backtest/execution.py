"""Market-order submission, latency-one fills, and non-trading outcomes.

Quantity is chosen when the execution open is known. Submission does not read
that later open. A missing open expires the order instead of moving it forward.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from quantforge.backtest.models import (
    BacktestDataError,
    BacktestIntegrityError,
    BarOpen,
    EvaluationWindow,
    ExecutionAssumptions,
    Fill,
    LedgerEntry,
    Order,
    OrderSide,
    OrderState,
    RejectionReason,
    due_time_for,
    fill_identity,
    order_identity,
)
from quantforge.backtest.portfolio import (
    Portfolio,
    plan_all_in_buy,
    quote_sell,
)
from quantforge.strategies.mean_reversion import MeanReversionSignal
from quantforge.strategies.models import NEXT_BAR_OPEN, SignalAction, StrategySignal


@dataclass(frozen=True)
class ExecutionOutcome:
    """What submission or resolution did at one phase."""

    name: str
    order: Order | None = None


class OrderBook:
    """At most one pending order. Terminal records stay in submission order."""

    def __init__(
        self,
        assumptions: ExecutionAssumptions,
        strategy_config_id: str,
        window: EvaluationWindow | None,
    ) -> None:
        self.assumptions = assumptions
        self.strategy_config_id = strategy_config_id
        self.window = window
        self.pending: Order | None = None
        self.orders: list[Order] = []
        self._known_ids: set[str] = set()

    def handle_signal(
        self,
        signal: StrategySignal | MeanReversionSignal,
        portfolio: Portfolio,
        now: datetime,
    ) -> ExecutionOutcome:
        """Submit, reject, cancel, or ignore a signal that is already available.

        The type gate is the only M8 change in this method. Order timing,
        fees, slippage, and the hold/cancel rules are unchanged.
        """
        if not isinstance(signal, (StrategySignal, MeanReversionSignal)):
            raise BacktestDataError("unsupported signal type")
        if signal.action is SignalAction.NO_SIGNAL:
            canceled = self.cancel_pending(now)
            if canceled is None:
                return ExecutionOutcome("no_op")
            return ExecutionOutcome("cancel", canceled)
        if signal.execution_policy != NEXT_BAR_OPEN or (
            signal.earliest_eligible_open != signal.available_at
        ):
            return self._reject(
                signal,
                OrderSide.BUY if signal.action is SignalAction.LONG else OrderSide.SELL,
                now,
                RejectionReason.INVALID_SIGNAL,
                quantity=None,
            )
        side = OrderSide.BUY if signal.action is SignalAction.LONG else OrderSide.SELL
        if side is OrderSide.BUY and portfolio.btc > 0:
            return ExecutionOutcome("no_op")
        if side is OrderSide.SELL and portfolio.btc == 0:
            return ExecutionOutcome("no_op")
        if self.pending is not None:
            return self._reject(
                signal,
                side,
                now,
                RejectionReason.DUPLICATE_ORDER,
                quantity=None,
            )
        due = due_time_for(signal.available_at)
        if due <= now:
            raise BacktestIntegrityError("latency-one due time must follow submission")
        if self.window is not None and not self.window.contains(due):
            return self._reject(
                signal,
                side,
                now,
                RejectionReason.OUTSIDE_EVALUATION_WINDOW,
                quantity=None,
            )
        order_id = order_identity(
            strategy_config_id=self.strategy_config_id,
            signal_open_time=signal.open_time,
            side=side,
            due_time=due,
        )
        if order_id in self._known_ids:
            return self._reject(
                signal,
                side,
                now,
                RejectionReason.DUPLICATE_ORDER,
                quantity=None,
            )
        order = Order(
            order_id=order_id,
            state=OrderState.SUBMITTED,
            side=side,
            signal_open_time=signal.open_time,
            submitted_at=now,
            due_time=due,
        )
        self._known_ids.add(order_id)
        self.orders.append(order)
        self.pending = order
        return ExecutionOutcome("submit", order)

    def resolve(
        self,
        now: datetime,
        open_view: BarOpen | None,
        portfolio: Portfolio,
    ) -> ExecutionOutcome | None:
        """Fill or expire an order whose due time is this timestamp."""
        if self.pending is None:
            return None
        if self.pending.due_time < now:
            raise BacktestIntegrityError(
                "pending order was not resolved at its due time"
            )
        if self.pending.due_time > now:
            return None
        if open_view is None:
            return self._finish(
                self.pending,
                OrderState.EXPIRED,
                RejectionReason.MISSING_EXECUTION_BAR,
                "expire",
            )
        if self.pending.side is OrderSide.BUY:
            return self._fill_buy(open_view, portfolio, now)
        return self._fill_sell(open_view, portfolio, now)

    def cancel_pending(self, now: datetime) -> Order | None:
        """Cancel an order that is still waiting for a later open.

        An order already filled or expired at ``now`` is no longer pending, so
        this cannot reverse it.
        """
        if self.pending is None or self.pending.due_time <= now:
            return None
        outcome = self._finish(
            self.pending,
            OrderState.CANCELED,
            RejectionReason.NO_EXECUTION_INTENT,
            "cancel",
        )
        return outcome.order

    def _fill_buy(
        self,
        open_view: BarOpen,
        portfolio: Portfolio,
        now: datetime,
    ) -> ExecutionOutcome:
        assert self.pending is not None
        quote = plan_all_in_buy(portfolio.cash, open_view.open, self.assumptions)
        if quote is None:
            return self._finish(
                self.pending,
                OrderState.REJECTED,
                RejectionReason.ZERO_QUANTITY,
                "reject",
            )
        cash_after = portfolio.cash - quote.posted_debit
        btc_after = portfolio.btc + quote.quantity
        fill, entry = self._documents(
            open_view,
            now,
            quantity=quote.quantity,
            execution_price=quote.execution_price,
            posted_notional=quote.posted_notional,
            posted_fee=quote.posted_fee,
            posted_cash_delta=-quote.posted_debit,
            rounding_residual=quote.rounding_residual,
            cash_after=cash_after,
            btc_after=btc_after,
            realized_delta=Decimal(0),
            btc_delta=quote.quantity,
        )
        portfolio.apply_buy(quote, fill, entry)
        finished = self._finish(
            self.pending,
            OrderState.FILLED,
            None,
            "fill",
            quantity=quote.quantity,
        )
        return finished

    def _fill_sell(
        self,
        open_view: BarOpen,
        portfolio: Portfolio,
        now: datetime,
    ) -> ExecutionOutcome:
        assert self.pending is not None
        if portfolio.btc <= 0:
            return self._finish(
                self.pending,
                OrderState.REJECTED,
                RejectionReason.INSUFFICIENT_POSITION,
                "reject",
            )
        quote = quote_sell(portfolio.btc, open_view.open, self.assumptions)
        if quote is None:
            return self._finish(
                self.pending,
                OrderState.REJECTED,
                RejectionReason.INSUFFICIENT_POSITION,
                "reject",
            )
        realized_delta = quote.posted_proceeds - portfolio.cost_total
        fill, entry = self._documents(
            open_view,
            now,
            quantity=quote.quantity,
            execution_price=quote.execution_price,
            posted_notional=quote.posted_notional,
            posted_fee=quote.posted_fee,
            posted_cash_delta=quote.posted_proceeds,
            rounding_residual=quote.rounding_residual,
            cash_after=portfolio.cash + quote.posted_proceeds,
            btc_after=Decimal(0),
            realized_delta=realized_delta,
            btc_delta=-quote.quantity,
        )
        portfolio.apply_sell(quote, fill, entry)
        return self._finish(
            self.pending,
            OrderState.FILLED,
            None,
            "fill",
            quantity=quote.quantity,
        )

    def _documents(
        self,
        open_view: BarOpen,
        now: datetime,
        *,
        quantity: Decimal,
        execution_price: Decimal,
        posted_notional: Decimal,
        posted_fee: Decimal,
        posted_cash_delta: Decimal,
        rounding_residual: Decimal,
        cash_after: Decimal,
        btc_after: Decimal,
        realized_delta: Decimal,
        btc_delta: Decimal,
    ) -> tuple[Fill, LedgerEntry]:
        assert self.pending is not None
        fill_id = fill_identity(self.pending.order_id)
        fill = Fill(
            fill_id=fill_id,
            order_id=self.pending.order_id,
            time=now,
            side=self.pending.side,
            quantity=quantity,
            raw_open=open_view.open,
            execution_price=execution_price,
            posted_notional=posted_notional,
            posted_fee=posted_fee,
            posted_cash_delta=posted_cash_delta,
            rounding_residual=rounding_residual,
            source_archive_sha256=open_view.source_archive_sha256,
            source_year=open_view.source_year,
            source_month=open_view.source_month,
            source_schema_version=open_view.source_schema_version,
            signal_open_time=self.pending.signal_open_time,
        )
        entry = LedgerEntry(
            entry_id=f"{fill_id}:ledger",
            fill_id=fill_id,
            time=now,
            cash_delta=posted_cash_delta,
            btc_delta=btc_delta,
            fee=posted_fee,
            realized_delta=realized_delta,
            cash_after=cash_after,
            btc_after=btc_after,
        )
        return fill, entry

    def _reject(
        self,
        signal: StrategySignal | MeanReversionSignal,
        side: OrderSide,
        now: datetime,
        reason: RejectionReason,
        quantity: Decimal | None,
    ) -> ExecutionOutcome:
        due = due_time_for(signal.available_at)
        order_id = order_identity(
            strategy_config_id=self.strategy_config_id,
            signal_open_time=signal.open_time,
            side=side,
            due_time=due,
        )
        if order_id in self._known_ids:
            order_id = f"{order_id}:rejected:{len(self.orders)}"
        order = Order(
            order_id=order_id,
            state=OrderState.REJECTED,
            side=side,
            signal_open_time=signal.open_time,
            submitted_at=now,
            due_time=due,
            reason=reason,
            quantity=quantity,
        )
        self._known_ids.add(order.order_id)
        self.orders.append(order)
        return ExecutionOutcome("reject", order)

    def _finish(
        self,
        order: Order,
        state: OrderState,
        reason: RejectionReason | None,
        name: str,
        quantity: Decimal | None = None,
    ) -> ExecutionOutcome:
        if order.state is not OrderState.SUBMITTED:
            raise BacktestIntegrityError(
                "order left the submitted state more than once"
            )
        finished = replace(
            order,
            state=state,
            reason=reason,
            quantity=order.quantity if quantity is None else quantity,
        )
        self.orders = [
            finished if item.order_id == order.order_id else item
            for item in self.orders
        ]
        if self.pending is not None and self.pending.order_id == order.order_id:
            self.pending = None
        return ExecutionOutcome(name, finished)
