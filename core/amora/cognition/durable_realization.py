"""Internal durable realization boundary; never a user-controlled metadata flag."""
from contextvars import ContextVar


# Only the authorized reply dispatcher may bind this exact turn ID.
DURABLE_REALIZATION: ContextVar[str | None] = ContextVar(
    "amora_durable_realization", default=None)


def durable_realization(request):
    return DURABLE_REALIZATION.get() == str(request.turn_id)


class DeferredRealization(RuntimeError):
    """No social response was committed; dispatcher owns the retry decision."""

    def __init__(self, reasons):
        self.reasons = tuple(reasons)
        super().__init__("DURABLE_REALIZATION_DEFERRED")
