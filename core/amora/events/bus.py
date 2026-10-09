from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .models import AmoraEvent

EventHandler = Callable[[AmoraEvent], Awaitable[None]]


class EventStore(Protocol):
    def append_event(self, event: AmoraEvent) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublishResult:
    event_id: str
    deduplicated: bool
    handlers_called: int
    errors: tuple[str, ...] = ()


class AsyncEventBus:
    def __init__(self, *, event_store: EventStore | None = None, dedupe_capacity: int = 4096) -> None:
        if dedupe_capacity <= 0:
            raise ValueError("dedupe_capacity must be positive")
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._dedupe_capacity = dedupe_capacity
        self._event_store = event_store
        self._lock = asyncio.Lock()

    def subscribe_bootstrap(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler before async runtime starts.

        Bootstrap wiring is single-threaded. Runtime changes must continue using
        subscribe()/unsubscribe() so mutation is protected by the async lock.
        """
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        async with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    async def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        async with self._lock:
            handlers = self._handlers.get(event_type)
            if not handlers:
                return
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                self._handlers.pop(event_type, None)

    async def publish(self, event: AmoraEvent, *, persist: bool = True) -> PublishResult:
        dedupe_key = event.dedupe_key or str(event.id)

        if persist and self._event_store is not None and not self._event_store.append_event(event):
            return PublishResult(str(event.id), True, 0)

        async with self._lock:
            if dedupe_key in self._seen:
                return PublishResult(str(event.id), True, 0)
            self._seen[dedupe_key] = None
            self._seen.move_to_end(dedupe_key)
            while len(self._seen) > self._dedupe_capacity:
                self._seen.popitem(last=False)
            handlers = tuple(self._handlers.get(event.event_type, ())) + tuple(self._handlers.get("*", ()))

        errors: list[str] = []
        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        return PublishResult(str(event.id), False, len(handlers), tuple(errors))
