"""Internal durable realization boundary; never a user-controlled metadata flag."""
from contextvars import ContextVar
from collections.abc import Awaitable, Callable


# Only the authorized reply dispatcher may bind this exact turn ID.
DURABLE_REALIZATION: ContextVar[str | None] = ContextVar(
    "amora_durable_realization", default=None)
DURABLE_AUTHORIZATION: ContextVar[Callable[[], Awaitable[bool]] | None] = ContextVar(
    "amora_durable_authorization", default=None)


# Only the text dispatcher binds this check. Compare the compiled causal
# evidence against committed inputs before realizing a response.
DURABLE_CAUSAL_CHECK: ContextVar[Callable[..., Awaitable[bool]] | None] = ContextVar(
    "amora_durable_causal_check", default=None)


def durable_realization(request):
    return DURABLE_REALIZATION.get() == str(request.turn_id)


class DeferredRealization(RuntimeError):
    """No social response was committed; dispatcher owns the retry decision."""

    def __init__(self, reasons, *, retry_after_seconds=None):
        self.reasons = tuple(reasons)
        self.retry_after_seconds = retry_after_seconds
        super().__init__("DURABLE_REALIZATION_DEFERRED")


class CausalReplanRequired(DeferredRealization):
    def __init__(self):
        super().__init__(["DURABLE_CAUSAL_CONTEXT_CHANGED"])


def adaptive_context_allowed(context):
    """Only the authorized durable text pilot can carry an oversized snapshot."""
    from amora.events import PrivacyScope
    return (DURABLE_REALIZATION.get() == str(context.turn_id)
            and DURABLE_AUTHORIZATION.get() is not None
            and context.speaker_id == "PERSON_BK"
            and context.target_id == "ENTITY_AMORA"
            and context.scene_visibility == "private"
            and context.privacy_scope == PrivacyScope.PERSONAL
            and context.remote_processing_allowed
            and not context.visual_observation and not context.web_research
            and not context.temporal_opportunity and not context.perceptual_expressions
            and len(context.user_text.encode("utf-8")) > 4096)
