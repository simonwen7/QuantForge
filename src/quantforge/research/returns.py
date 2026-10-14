"""Close-to-close return calculations using explicit Decimal precision."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from quantforge.research.models import (
    RETURN_DECIMAL_PRECISION,
    MissingReason,
    PriceObservation,
    ResearchDataError,
    ReturnCalculationError,
    ReturnKind,
    ReturnObservation,
    candle_available_at,
)

_HOUR = timedelta(hours=1)


def return_context() -> Context:
    """Return the fixed high-precision context used for return arithmetic."""
    context = Context(prec=RETURN_DECIMAL_PRECISION)
    context.traps[InvalidOperation] = True
    context.traps[DivisionByZero] = True
    context.traps[Overflow] = True
    return context


def _validate_price(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal):
        raise ReturnCalculationError(f"{label} price must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise ReturnCalculationError(f"{label} price must be finite and positive")


def simple_return(previous_close: Decimal, current_close: Decimal) -> Decimal:
    """Calculate ``current / previous - 1`` at the research Decimal precision."""
    _validate_price(previous_close, "previous")
    _validate_price(current_close, "current")
    with localcontext(return_context()):
        return current_close / previous_close - Decimal(1)


def log_return(previous_close: Decimal, current_close: Decimal) -> Decimal:
    """Calculate ``ln(current) - ln(previous)`` at the research Decimal precision."""
    _validate_price(previous_close, "previous")
    _validate_price(current_close, "current")
    with localcontext(return_context()):
        return current_close.ln() - previous_close.ln()


def calculate_returns(
    observations: Sequence[PriceObservation],
    kind: ReturnKind,
) -> tuple[ReturnObservation, ...]:
    """Align returns to the current candle without crossing missing hours.

    The first observation has no previous close. A later observation whose open
    time is not exactly one hour after its predecessor is an explicit gap, not
    a return against an older price.
    """
    results: list[ReturnObservation] = []
    previous: PriceObservation | None = None
    for observation in observations:
        if previous is not None and observation.open_time <= previous.open_time:
            raise ResearchDataError("observations must be strictly chronological")
        if previous is None:
            value = None
            reason = MissingReason.INITIAL
        elif observation.open_time != previous.open_time + _HOUR:
            value = None
            reason = MissingReason.GAP
        elif kind is ReturnKind.SIMPLE:
            value = simple_return(previous.close, observation.close)
            reason = None
        elif kind is ReturnKind.LOG:
            value = log_return(previous.close, observation.close)
            reason = None
        else:
            raise ReturnCalculationError(f"unsupported return kind: {kind!r}")
        results.append(
            ReturnObservation(
                open_time=observation.open_time,
                close_time=observation.close_time,
                available_at=candle_available_at(observation.open_time),
                kind=kind,
                value=value,
                missing_reason=reason,
            )
        )
        previous = observation
    return tuple(results)
