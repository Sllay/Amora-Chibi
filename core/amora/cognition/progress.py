"""Ephemeral per-request provider progress without leaking token content.

The output is deliberately restricted to fixed stage labels: no reasoning,
partial model text, user input, prompt, credentials, or model error messages.
Neither a callback nor a progress payload is persisted in Core memory.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable, Literal

ProgressStage = Literal["reasoning", "writing"]
ProgressSink = Callable[[ProgressStage], None]

_progress_sink: ContextVar[ProgressSink | None] = ContextVar(
    "amora_social_progress_sink", default=None
)


def bind_social_progress(sink: ProgressSink) -> Token[ProgressSink | None]:
    return _progress_sink.set(sink)


def unbind_social_progress(token: Token[ProgressSink | None]) -> None:
    _progress_sink.reset(token)


def report_social_progress(stage: ProgressStage) -> None:
    """Best-effort side-channel for UI status; never required for cognition."""
    if stage not in ("reasoning", "writing"):
        return
    sink = _progress_sink.get()
    if sink is not None:
        try:
            sink(stage)
        except Exception:
            # UI disconnection or a full queue must not affect the provider.
            pass
