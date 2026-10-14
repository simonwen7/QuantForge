"""Single-threaded latency-one backtest.

At timestamp T the phases are: reveal an open if one exists, resolve an order
due at T from that open only, release the prior bar's completed OHLCV, release
the M5 signal, submit a new order when the position and policy allow it, then
mark equity with a price that is already available. Completion is scheduled
from the completed bar and does not require the next open.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from quantforge.backtest.events import (
    BAR_COMPLETE,
    BAR_OPEN,
    CANCEL,
    EXPIRE,
    FILL,
    NO_OP,
    REJECT,
    SIGNAL,
    STALE_VALUATION,
    SUBMIT,
    VALUATION,
)
from quantforge.backtest.execution import OrderBook
from quantforge.backtest.models import (
    ENGINE_VERSION,
    NO_SIGNAL_POLICY,
    BacktestDataError,
    BacktestIntegrityError,
    BacktestResult,
    BarOpen,
    EngineEvent,
    EquityPoint,
    EvaluationWindow,
    EventPhase,
    ExecutionAssumptions,
    MarketGap,
    StaleValuation,
    bar_complete_view,
    bar_open_view,
    format_decimal,
    format_utc,
    run_identity,
)
from quantforge.backtest.portfolio import Portfolio, accounting_context
from quantforge.backtest.replay import prepare_bars, prepare_signals
from quantforge.research.market_bars import DataOrigin, MarketBar, MarketBarDataset
from quantforge.research.models import (
    HoldoutAccessError,
    PartitionLineage,
    ResearchAccessMode,
)
from quantforge.strategies.models import StrategySignal

_HOUR = timedelta(hours=1)
_PHASE_NAMES = {
    "bar_open": (EventPhase.BAR_OPEN, BAR_OPEN),
    "fill": (EventPhase.FILL_DUE, FILL),
    "expire": (EventPhase.FILL_DUE, EXPIRE),
    "reject_due": (EventPhase.FILL_DUE, REJECT),
    "bar_complete": (EventPhase.BAR_COMPLETE, BAR_COMPLETE),
    "signal": (EventPhase.SIGNAL, SIGNAL),
    "submit": (EventPhase.ORDER_SUBMISSION, SUBMIT),
    "reject": (EventPhase.ORDER_SUBMISSION, REJECT),
    "cancel": (EventPhase.ORDER_SUBMISSION, CANCEL),
    "no_op": (EventPhase.ORDER_SUBMISSION, NO_OP),
    "valuation": (EventPhase.VALUATION, VALUATION),
    "stale_valuation": (EventPhase.VALUATION, STALE_VALUATION),
}


class BacktestEngine:
    """Replay one causal path and post a ledger. This is not a matching engine."""

    def run_synthetic(
        self,
        bars: Sequence[MarketBar],
        signals: Sequence[StrategySignal],
        assumptions: ExecutionAssumptions | None = None,
        window: EvaluationWindow | None = None,
    ) -> BacktestResult:
        """Run caller-built bars. The result is labeled synthetic and unverified."""
        assumptions = assumptions or ExecutionAssumptions()
        return self._run(
            bars,
            signals,
            assumptions,
            window,
            origin=DataOrigin.SYNTHETIC_UNVERIFIED,
            lineage_payload={
                "origin": DataOrigin.SYNTHETIC_UNVERIFIED.value,
                "bars": [_synthetic_bar_identity(bar) for bar in bars],
            },
        )

    def run_verified(
        self,
        dataset: MarketBarDataset,
        signals: Sequence[StrategySignal],
        assumptions: ExecutionAssumptions | None = None,
        window: EvaluationWindow | None = None,
    ) -> BacktestResult:
        """Run a loader dataset. Final-holdout evaluation stays unauthorized."""
        self._reject_holdout(dataset)
        assumptions = assumptions or ExecutionAssumptions()
        return self._run(
            dataset.bars,
            signals,
            assumptions,
            window,
            origin=DataOrigin.VERIFIED_STORAGE,
            lineage_payload={
                "origin": DataOrigin.VERIFIED_STORAGE.value,
                "partitions": [_partition_identity(item) for item in dataset.lineage],
            },
        )

    def _run(
        self,
        bars: Sequence[MarketBar],
        signals: Sequence[StrategySignal],
        assumptions: ExecutionAssumptions,
        window: EvaluationWindow | None,
        *,
        origin: DataOrigin,
        lineage_payload: dict[str, object],
    ) -> BacktestResult:
        prepared = prepare_bars(bars)
        signal_index = prepare_signals(signals, prepared)
        strategy_config_id = _one_strategy_id(signals)
        opens = {bar.open_time: bar for bar in prepared}
        completions: dict[datetime, MarketBar] = {}
        for bar in prepared:
            if bar.available_at in completions:
                raise BacktestDataError("duplicate bar completion timestamp")
            completions[bar.available_at] = bar
        book = OrderBook(assumptions, strategy_config_id, window)
        portfolio = Portfolio(
            cash=assumptions.initial_cash,
            cash_quantum=assumptions.cash_quantum,
        )
        events: list[EngineEvent] = []
        gaps: list[MarketGap] = []
        equity_points: list[EquityPoint] = []
        stale: list[StaleValuation] = []
        scheduled: list[datetime] = []
        known_times: set[datetime] = set()
        last_open: datetime | None = None
        last_price: Decimal | None = None
        last_price_time: datetime | None = None
        last_price_open: datetime | None = None
        stale_emitted = False

        def push(timestamp: datetime) -> None:
            if timestamp not in known_times:
                known_times.add(timestamp)
                heapq.heappush(scheduled, timestamp)

        for bar in prepared:
            push(bar.open_time)
            push(bar.available_at)

        while scheduled:
            now = heapq.heappop(scheduled)
            bar = opens.get(now)
            open_view: BarOpen | None = None
            observed_gap = False
            if bar is not None:
                if last_open is not None and bar.open_time != last_open + _HOUR:
                    gaps.append(MarketGap(last_open, bar.open_time))
                    observed_gap = True
                open_view = bar_open_view(bar)
                events.append(
                    EngineEvent(
                        time=now,
                        phase=EventPhase.BAR_OPEN,
                        name=BAR_OPEN,
                        open_view=open_view,
                    )
                )
                last_open = bar.open_time
            elif last_open is not None and now > last_open + _HOUR:
                observed_gap = True

            resolution = book.resolve(now, open_view, portfolio)
            if resolution is not None and resolution.order is not None:
                phase, name = _PHASE_NAMES[
                    "reject_due" if resolution.name == "reject" else resolution.name
                ]
                events.append(
                    EngineEvent(
                        time=now,
                        phase=phase,
                        name=name,
                        order_id=resolution.order.order_id,
                    )
                )

            completed = completions.get(now)
            if completed is not None:
                events.append(
                    EngineEvent(
                        time=now,
                        phase=EventPhase.BAR_COMPLETE,
                        name=BAR_COMPLETE,
                        complete_view=bar_complete_view(completed),
                    )
                )

            signal = signal_index.get(now)
            if signal is not None:
                if completed is None or completed.open_time != signal.open_time:
                    raise BacktestDataError(
                        "signal was released without its completed decision bar"
                    )
                events.append(
                    EngineEvent(
                        time=now,
                        phase=EventPhase.SIGNAL,
                        name=SIGNAL,
                        order_id=None,
                    )
                )
                outcome = book.handle_signal(signal, portfolio, now)
                phase, name = _PHASE_NAMES[outcome.name]
                events.append(
                    EngineEvent(
                        time=now,
                        phase=phase,
                        name=name,
                        order_id=None
                        if outcome.order is None
                        else outcome.order.order_id,
                    )
                )
                if outcome.name == "submit" and outcome.order is not None:
                    push(outcome.order.due_time)

            if completed is not None:
                price = completed.close
                equity = portfolio.equity(price)
                point = EquityPoint(
                    time=now,
                    price=price,
                    price_open_time=completed.open_time,
                    cash=portfolio.cash,
                    btc=portfolio.btc,
                    equity=equity,
                    fresh=True,
                    in_evaluation=window is None or window.contains(now),
                )
                with localcontext(accounting_context()):
                    if point.equity != point.cash + point.btc * point.price:
                        raise BacktestIntegrityError("equity point does not reconcile")
                equity_points.append(point)
                events.append(
                    EngineEvent(
                        time=now,
                        phase=EventPhase.VALUATION,
                        name=VALUATION,
                    )
                )
                last_price = price
                last_price_time = now
                last_price_open = completed.open_time
                stale_emitted = False
            elif (
                observed_gap
                and portfolio.btc > 0
                and last_price is not None
                and last_price_time is not None
                and last_price_open is not None
                and not stale_emitted
            ):
                stale.append(
                    StaleValuation(
                        time=now,
                        last_price=last_price,
                        last_price_time=last_price_time,
                        cash=portfolio.cash,
                        btc=portfolio.btc,
                    )
                )
                events.append(
                    EngineEvent(
                        time=now,
                        phase=EventPhase.VALUATION,
                        name=STALE_VALUATION,
                    )
                )
                stale_emitted = True

        _reconcile(portfolio, assumptions.initial_cash)
        result = BacktestResult(
            run_id="",
            data_origin=origin,
            engine_version=ENGINE_VERSION,
            strategy_config_id=strategy_config_id,
            assumptions=assumptions,
            events=tuple(events),
            orders=tuple(book.orders),
            fills=tuple(portfolio.fills or ()),
            ledger=tuple(portfolio.ledger or ()),
            equity_points=tuple(equity_points),
            stale_valuations=tuple(stale),
            market_gaps=tuple(gaps),
            final_cash=portfolio.cash,
            final_btc=portfolio.btc,
            realized_pnl=portfolio.realized_pnl,
            accumulated_fees=portfolio.fees,
        )
        payload = _identity_payload(
            result,
            window,
            {
                **lineage_payload,
                "signals": [_signal_identity(signal) for signal in signals],
            },
        )
        return BacktestResult(
            run_id=run_identity(payload),
            data_origin=result.data_origin,
            engine_version=result.engine_version,
            strategy_config_id=result.strategy_config_id,
            assumptions=result.assumptions,
            events=result.events,
            orders=result.orders,
            fills=result.fills,
            ledger=result.ledger,
            equity_points=result.equity_points,
            stale_valuations=result.stale_valuations,
            market_gaps=result.market_gaps,
            final_cash=result.final_cash,
            final_btc=result.final_btc,
            realized_pnl=result.realized_pnl,
            accumulated_fees=result.accumulated_fees,
        )

    @staticmethod
    def _reject_holdout(dataset: MarketBarDataset) -> None:
        if dataset.origin is not DataOrigin.VERIFIED_STORAGE:
            raise BacktestDataError("verified replay requires a loader dataset")
        if dataset.access_mode is ResearchAccessMode.FINAL_EVALUATION:
            raise HoldoutAccessError("M6 does not evaluate the final holdout")
        if dataset.access_mode is not ResearchAccessMode.DEVELOPMENT:
            raise BacktestDataError(
                f"unsupported research access mode: {dataset.access_mode!r}"
            )
        if dataset.final_evaluation_authorized:
            raise HoldoutAccessError(
                "development mode cannot authorize final-holdout access"
            )
        periods = tuple(dataset.covered_periods) + tuple(dataset.expected_periods)
        if any(period.year >= 2025 for period in periods):
            raise HoldoutAccessError(
                "development mode cannot materialize final-holdout observations"
            )
        if any(
            bar.source_year >= 2025 or bar.open_time.year >= 2025
            for bar in dataset.bars
        ):
            raise HoldoutAccessError(
                "development mode cannot materialize final-holdout observations"
            )


def _one_strategy_id(signals: Sequence[StrategySignal]) -> str:
    if not signals:
        return "no-strategy"
    identifier = signals[0].strategy_config_id
    if any(signal.strategy_config_id != identifier for signal in signals):
        raise BacktestDataError("a replay cannot mix strategy configuration identities")
    return identifier


def _synthetic_bar_identity(bar: MarketBar) -> dict[str, object]:
    return {
        "close": format_decimal(bar.close),
        "close_time": format_utc(bar.close_time),
        "high": format_decimal(bar.high),
        "low": format_decimal(bar.low),
        "open": format_decimal(bar.open),
        "open_time": format_utc(bar.open_time),
        "source_archive_sha256": bar.source_archive_sha256,
        "source_month": bar.source_month,
        "source_schema_version": bar.source_schema_version,
        "source_year": bar.source_year,
        "volume": format_decimal(bar.volume),
    }


def _signal_identity(signal: StrategySignal) -> dict[str, object]:
    return {
        "action": signal.action.value,
        "available_at": format_utc(signal.available_at),
        "momentum": None
        if signal.momentum is None
        else format_decimal(signal.momentum),
        "no_signal_reason": None
        if signal.no_signal_reason is None
        else signal.no_signal_reason.value,
        "open_time": format_utc(signal.open_time),
        "strategy_config_id": signal.strategy_config_id,
    }


def _partition_identity(item: PartitionLineage) -> dict[str, object]:
    return {
        "month": item.month,
        "observation_count": item.observation_count,
        "parquet_sha256": item.parquet_sha256,
        "source_archive_sha256": item.source_archive_sha256,
        "source_provenance_sha256": item.source_provenance_sha256,
        "validation_sha256": item.validation_sha256,
        "warning_count": item.warning_count,
        "year": item.year,
    }


def _identity_payload(
    result: BacktestResult,
    window: EvaluationWindow | None,
    lineage_payload: dict[str, object],
) -> dict[str, object]:
    assumptions = result.assumptions
    return {
        "cash_quantum": format_decimal(assumptions.cash_quantum),
        "data_origin": result.data_origin.value,
        "engine_version": ENGINE_VERSION,
        "evaluation_window": None
        if window is None
        else [format_utc(window.start), format_utc(window.end)],
        "fee_rate": format_decimal(assumptions.fee_rate),
        "initial_cash": format_decimal(assumptions.initial_cash),
        "latency_bars": assumptions.latency_bars,
        "lineage": lineage_payload,
        "no_signal_policy": NO_SIGNAL_POLICY,
        "quantity_quantum": format_decimal(assumptions.quantity_quantum),
        "slippage_rate": format_decimal(assumptions.slippage_rate),
        "strategy_config_id": result.strategy_config_id,
    }


def _reconcile(portfolio: Portfolio, initial_cash: Decimal) -> None:
    fills = portfolio.fills or []
    ledger = portfolio.ledger or []
    if len(fills) != len(ledger):
        raise BacktestIntegrityError("every fill must have one ledger entry")
    if len({fill.fill_id for fill in fills}) != len(fills):
        raise BacktestIntegrityError("fill identifiers must be unique")
    cash = initial_cash
    fees = Decimal(0)
    for entry in ledger:
        cash += entry.cash_delta
        fees += entry.fee
    if cash != portfolio.cash:
        raise BacktestIntegrityError("ledger cash does not reconcile")
    if fees != portfolio.fees:
        raise BacktestIntegrityError("ledger fees do not reconcile")
    if portfolio.btc == 0 and portfolio.cost_total != 0:
        raise BacktestIntegrityError("flat book still carries acquisition cost")
