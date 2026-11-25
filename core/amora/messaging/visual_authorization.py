"""Context-local authorization fence for visual requests and fallback."""
from contextvars import ContextVar

from .groq_vision import GroqVisionError

VISUAL_AUTHORIZATION = ContextVar("amora_visual_authorization", default=None)


async def require_visual_authorization():
    guard = VISUAL_AUTHORIZATION.get()
    # Direct adapter probes remain supported. The owner-DM service always
    # installs a guard before entering any remote visual adapter.
    if guard is not None and not await guard():
        raise GroqVisionError("INCOMING_VISION_AUTHORIZATION_LOST",
                              "visual authorization no longer valid")
