"""Order prices, no-ops, duplicates, and cancellation ordering."""

from datetime import UTC, datetime
from decimal import Decimal

from quantforge.backtest.execution import OrderBook
from quantforge.backtest.models import (
    BarOpen,
    EvaluationWindow,
    ExecutionAssumptions,
    OrderSide,
    OrderState,
    RejectionReason,
)
from quantforge.backtest.portfolio import Portfolio
from quantforge.strategies.models import SignalAction
from tests.backtest.support import HOUR, directional_signal, warmup_signal

START = datetime(2024, 6, 1, tzinfo=UTC)


def _open(when: datetime, price: str) -> BarOpen:
    return BarOpen(
        open_time=when,
        open=Decimal(price),
        source_archive_sha256="b" * 64,
        source_year=when.year,
        source_month=when.month,
        source_schema_version="synthetic-unverified",
    )


def test_buy_and_sell_use_adverse_open_prices():
    book = OrderBook(ExecutionAssumptions(), "cfg", None)
    portfolio = Portfolio(cash=Decimal("100.15005"))
    book.handle_signal(
        directional_signal(START, SignalAction.LONG, "0.1"), portfolio, START + HOUR
    )
    buy = book.resolve(START + 2 * HOUR, _open(START + 2 * HOUR, "100"), portfolio)

    assert buy is not None and buy.order is not None
    assert buy.order.state is OrderState.FILLED
    assert portfolio.fills[0].raw_open == Decimal(100)
    assert portfolio.fills[0].execution_price == Decimal("100.05")
    assert portfolio.fills[0].posted_fee == Decimal("0.10005")
    assert portfolio.cash == Decimal(0)
    assert portfolio.btc == Decimal(1)
    assert not hasattr(portfolio.fills[0], "high")
    assert not hasattr(portfolio.fills[0], "close")
    assert not hasattr(portfolio.fills[0], "volume")

    book.handle_signal(
        directional_signal(START + HOUR, SignalAction.FLAT, "0"),
        portfolio,
        START + 2 * HOUR,
    )
    sell = book.resolve(START + 3 * HOUR, _open(START + 3 * HOUR, "110"), portfolio)

    assert sell is not None and sell.order is not None
    assert sell.order.side is OrderSide.SELL
    assert portfolio.fills[1].execution_price == Decimal("109.945")
    assert portfolio.fills[1].posted_cash_delta == Decimal("109.835055")
    assert portfolio.btc == Decimal(0)
    assert portfolio.realized_pnl == Decimal("9.685005")


def test_repeated_directional_signals_do_not_trade_twice():
    book = OrderBook(ExecutionAssumptions(), "cfg", None)
    portfolio = Portfolio(cash=Decimal("100.15005"))
    book.handle_signal(
        directional_signal(START, SignalAction.LONG, "0.1"), portfolio, START + HOUR
    )
    book.resolve(START + 2 * HOUR, _open(START + 2 * HOUR, "100"), portfolio)
    repeated = book.handle_signal(
        directional_signal(START + HOUR, SignalAction.LONG, "0.2"),
        portfolio,
        START + 2 * HOUR,
    )

    assert repeated.name == "no_op"
    assert len(portfolio.fills) == 1

    book.handle_signal(
        directional_signal(START + 2 * HOUR, SignalAction.FLAT, "-0.1"),
        portfolio,
        START + 3 * HOUR,
    )
    book.resolve(START + 4 * HOUR, _open(START + 4 * HOUR, "110"), portfolio)
    again = book.handle_signal(
        directional_signal(START + 3 * HOUR, SignalAction.FLAT, "-0.2"),
        portfolio,
        START + 4 * HOUR,
    )

    assert again.name == "no_op"
    assert len(portfolio.fills) == 2
    assert portfolio.btc == Decimal(0)


def test_duplicate_submission_is_rejected_and_a_later_no_signal_cancels_pending():
    book = OrderBook(ExecutionAssumptions(), "cfg", None)
    portfolio = Portfolio(cash=Decimal(10000))
    signal = directional_signal(START, SignalAction.LONG, "0.1")
    now = START + HOUR
    first = book.handle_signal(signal, portfolio, now)
    second = book.handle_signal(signal, portfolio, now)

    assert first.name == "submit"
    assert second.name == "reject"
    assert second.order is not None
    assert second.order.reason is RejectionReason.DUPLICATE_ORDER
    assert portfolio.cash == Decimal(10000)
    assert portfolio.btc == Decimal(0)

    canceled = book.handle_signal(warmup_signal(START + HOUR), portfolio, now)
    assert canceled.name == "cancel"
    assert canceled.order is not None
    assert canceled.order.state is OrderState.CANCELED
    assert book.pending is None
    assert portfolio.cash == Decimal(10000)
    assert portfolio.btc == Decimal(0)


def test_outside_window_and_missing_bar_do_not_move_cash():
    portfolio = Portfolio(cash=Decimal(10000))
    train = EvaluationWindow(
        datetime(2021, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, tzinfo=UTC),
    )
    book = OrderBook(ExecutionAssumptions(), "cfg", train)
    early = directional_signal(
        datetime(2023, 12, 31, 22, tzinfo=UTC), SignalAction.LONG, "0.1"
    )
    rejected = book.handle_signal(
        early, portfolio, datetime(2023, 12, 31, 23, tzinfo=UTC)
    )

    assert rejected.order is not None
    assert rejected.order.reason is RejectionReason.OUTSIDE_EVALUATION_WINDOW
    assert rejected.order.state is OrderState.REJECTED
    assert portfolio.cash == Decimal(10000)

    open_book = OrderBook(ExecutionAssumptions(), "cfg", None)
    submitted = open_book.handle_signal(
        directional_signal(START, SignalAction.LONG, "0.1"),
        portfolio,
        START + HOUR,
    )
    assert submitted.order is not None
    expired = open_book.resolve(submitted.order.due_time, None, portfolio)

    assert expired is not None and expired.order is not None
    assert expired.order.state is OrderState.EXPIRED
    assert expired.order.reason is RejectionReason.MISSING_EXECUTION_BAR
    assert portfolio.btc == Decimal(0)
    assert portfolio.cash == Decimal(10000)


def test_zero_cash_quantum_and_a_vanished_position_do_not_post():
    book = OrderBook(
        ExecutionAssumptions(initial_cash=Decimal("0.00000001")),
        "cfg",
        None,
    )
    portfolio = Portfolio(cash=Decimal("0.00000001"))
    submitted = book.handle_signal(
        directional_signal(START, SignalAction.LONG, "0.1"),
        portfolio,
        START + HOUR,
    )
    rejected = book.resolve(START + 2 * HOUR, _open(START + 2 * HOUR, "100"), portfolio)

    assert submitted.name == "submit"
    assert rejected is not None and rejected.order is not None
    assert rejected.order.state is OrderState.REJECTED
    assert rejected.order.reason is RejectionReason.ZERO_QUANTITY
    assert portfolio.cash == Decimal("0.00000001")
    assert portfolio.fills == []

    held = Portfolio(cash=Decimal("100.15005"))
    seller = OrderBook(ExecutionAssumptions(), "cfg", None)
    seller.handle_signal(
        directional_signal(START, SignalAction.LONG, "0.1"),
        held,
        START + HOUR,
    )
    seller.resolve(START + 2 * HOUR, _open(START + 2 * HOUR, "100"), held)
    seller.handle_signal(
        directional_signal(START + HOUR, SignalAction.FLAT, "0"),
        held,
        START + 2 * HOUR,
    )
    held.btc = Decimal(0)
    held.cost_total = Decimal(0)
    cash_before = held.cash
    blocked = seller.resolve(START + 3 * HOUR, _open(START + 3 * HOUR, "110"), held)

    assert blocked is not None and blocked.order is not None
    assert blocked.order.reason is RejectionReason.INSUFFICIENT_POSITION
    assert held.cash == cash_before
    assert held.btc == Decimal(0)
    assert len(held.fills) == 1
