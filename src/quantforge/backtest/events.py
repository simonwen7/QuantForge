"""Event names for the single-threaded replay.

Completion is a property of a bar that has already been seen. Emitting it does
not require the next bar's open to exist.
"""

BAR_OPEN = "bar_open"
FILL = "fill"
EXPIRE = "expire"
BAR_COMPLETE = "bar_complete"
SIGNAL = "signal"
SUBMIT = "submit"
REJECT = "reject"
CANCEL = "cancel"
NO_OP = "no_op"
VALUATION = "valuation"
STALE_VALUATION = "stale_valuation"
