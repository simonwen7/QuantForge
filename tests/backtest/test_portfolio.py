"""Hand-calculated cash, fee, cost, and equity checks."""

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

import pytest

from quantforge.backtest.models import (
    BacktestIntegrityError,
    ExecutionAssumptions,
    Fill,
    LedgerEntry,
    OrderSide,
)
from quantforge.backtest.portfolio import (
    Portfolio,
    plan_all_in_buy,
    quote_buy,
    quote_sell,
)

WHEN = datetime(2024, 6, 1, tzinfo=UTC)


def _post_buy(portfolio: Portfolio, quote, when) -> None:
    cash_after = portfolio.cash - quote.posted_debit
    btc_after = portfolio.btc + quote.quantity
    fill = Fill(
        fill_id="buy-1",
        order_id="order-1",
        time=when,
        side=OrderSide.BUY,
        quantity=quote.quantity,
        raw_open=quote.raw_open,
        execution_price=quote.execution_price,
        posted_notional=quote.posted_notional,
        posted_fee=quote.posted_fee,
        posted_cash_delta=-quote.posted_debit,
        rounding_residual=quote.rounding_residual,
        source_archive_sha256="a" * 64,
        source_year=2024,
        source_month=6,
        source_schema_version="synthetic-unverified",
        signal_open_time=when,
    )
    entry = LedgerEntry(
        entry_id="buy-1:ledger",
        fill_id="buy-1",
        time=when,
        cash_delta=-quote.posted_debit,
        btc_delta=quote.quantity,
        fee=quote.posted_fee,
        realized_delta=Decimal(0),
        cash_after=cash_after,
        btc_after=btc_after,
    )
    portfolio.apply_buy(quote, fill, entry)


def _post_sell(portfolio: Portfolio, quote, when) -> None:
    realized = quote.posted_proceeds - portfolio.cost_total
    fill = Fill(
        fill_id="sell-1",
        order_id="order-2",
        time=when,
        side=OrderSide.SELL,
        quantity=quote.quantity,
        raw_open=quote.raw_open,
        execution_price=quote.execution_price,
        posted_notional=quote.posted_notional,
        posted_fee=quote.posted_fee,
        posted_cash_delta=quote.posted_proceeds,
        rounding_residual=quote.rounding_residual,
        source_archive_sha256="a" * 64,
        source_year=2024,
        source_month=6,
        source_schema_version="synthetic-unverified",
        signal_open_time=when,
    )
    entry = LedgerEntry(
        entry_id="sell-1:ledger",
        fill_id="sell-1",
        time=when,
        cash_delta=quote.posted_proceeds,
        btc_delta=-quote.quantity,
        fee=quote.posted_fee,
        realized_delta=realized,
        cash_after=portfolio.cash + quote.posted_proceeds,
        btc_after=Decimal(0),
    )
    portfolio.apply_sell(quote, fill, entry)


def test_one_btc_round_trip_matches_the_hand_calculation():
    assumptions = ExecutionAssumptions()
    portfolio = Portfolio(cash=Decimal(10000))
    buy = quote_buy(Decimal(1), Decimal(100), assumptions)
    sell_preview = quote_sell(Decimal(1), Decimal(110), assumptions)

    assert buy is not None
    assert sell_preview is not None
    assert buy.execution_price == Decimal("100.05")
    assert buy.posted_fee == Decimal("0.10005")
    assert buy.posted_debit == Decimal("100.15005")
    assert buy.posted_notional == Decimal("100.05")
    assert sell_preview.execution_price == Decimal("109.945")
    assert sell_preview.posted_fee == Decimal("0.109945")
    assert sell_preview.posted_proceeds == Decimal("109.835055")

    _post_buy(portfolio, buy, WHEN)
    assert portfolio.cash == Decimal("9899.84995")
    assert portfolio.btc == Decimal(1)
    assert portfolio.cost_total == Decimal("100.15005")
    assert portfolio.fees == Decimal("0.10005")
    assert portfolio.equity(Decimal("100.05")) == Decimal(10000) - Decimal("0.10005")

    _post_sell(portfolio, sell_preview, WHEN)
    assert portfolio.btc == Decimal(0)
    assert portfolio.cost_total == Decimal(0)
    assert portfolio.realized_pnl == Decimal("9.685005")
    assert portfolio.cash == Decimal("10009.685005")
    assert portfolio.fees == Decimal("0.209995")
    assert buy.rounding_residual == Decimal(0)
    assert sell_preview.rounding_residual == Decimal(0)
    quantum = Decimal("0.00000001")
    assert portfolio.cash % quantum == Decimal(0)
    assert portfolio.equity(Decimal(110)) == portfolio.cash
    assert sum(
        entry.cash_delta for entry in portfolio.ledger
    ) == portfolio.cash - Decimal(10000)
    assert sum(entry.fee for entry in portfolio.ledger) == portfolio.fees


def test_all_in_buy_reserves_the_fee_and_cannot_overspend():
    assumptions = ExecutionAssumptions()
    cash = Decimal(10000)
    raw_open = Decimal(100)
    buy_price = raw_open * (Decimal(1) + assumptions.slippage_rate)
    unit = buy_price * (Decimal(1) + assumptions.fee_rate)
    raw_quantity = cash / unit
    expected = (raw_quantity / assumptions.quantity_quantum).to_integral_value(
        rounding=ROUND_DOWN
    ) * assumptions.quantity_quantum
    quote = plan_all_in_buy(cash, raw_open, assumptions)

    assert quote is not None
    assert quote.quantity == expected
    assert quote.quantity > 0
    assert quote.posted_debit <= cash
    assert quote.posted_debit >= quote.quantity * buy_price * (
        Decimal(1) + assumptions.fee_rate
    )
    assert (cash - quote.posted_debit) % assumptions.cash_quantum == Decimal(0)
    assert Decimal(0) <= quote.rounding_residual < assumptions.cash_quantum
    unrounded_quantity = cash / buy_price
    assert unrounded_quantity * buy_price * (Decimal(1) + assumptions.fee_rate) > cash


def test_second_posting_of_the_same_fill_is_rejected():
    assumptions = ExecutionAssumptions()
    portfolio = Portfolio(cash=Decimal(10000))
    buy = quote_buy(Decimal(1), Decimal(100), assumptions)
    assert buy is not None
    _post_buy(portfolio, buy, WHEN)

    with pytest.raises(BacktestIntegrityError, match="already posted"):
        _post_buy(portfolio, buy, WHEN)
    assert portfolio.btc == Decimal(1)
    assert portfolio.cash == Decimal("9899.84995")


def test_cash_and_position_cannot_be_driven_negative():
    portfolio = Portfolio(cash=Decimal(1))
    buy = quote_buy(Decimal(1), Decimal(100), ExecutionAssumptions())
    assert buy is not None

    with pytest.raises(BacktestIntegrityError, match="exceeds cash"):
        _post_buy(portfolio, buy, WHEN)
    assert portfolio.cash == Decimal(1)
    assert portfolio.btc == Decimal(0)
