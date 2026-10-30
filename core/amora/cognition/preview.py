from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


PreviewSink = Callable[[str], Awaitable[None] | None]
_preview_sink: ContextVar[PreviewSink | None] = ContextVar("amora_cognitive_preview_sink", default=None)


@contextmanager
def bind_preview_sink(sink: PreviewSink | None) -> Iterator[None]:
    """Bind an ephemeral surface-preview sink to the current request context.

    Preview text is never canonical state, never persisted by this module and
    never authorizes speech. It exists only so a UI can show generation progress
    while the complete realization frame is still awaiting local validation.
    """
    token = _preview_sink.set(sink)
    try:
        yield
    finally:
        _preview_sink.reset(token)


def preview_enabled() -> bool:
    return _preview_sink.get() is not None


async def publish_preview(text: str) -> None:
    sink = _preview_sink.get()
    clean = text.strip()
    if sink is None or not clean:
        return
    result = sink(clean)
    if inspect.isawaitable(result):
        await result
