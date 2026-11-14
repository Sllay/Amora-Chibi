"""Bounded, independently owned storage lane for cognition-only DB operations.

The lane never runs a compiler, emotion engine, provider or event subscriber.
A cancelled caller joins an already-started operation before unwinding; a
failed write is never replayed. Connections are created and closed on the lane.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from amora.latency_trace import span

_ALLOWED = frozenset({"list_messages", "get_message_by_dedupe", "put_message",
                      "put_journal", "list_segments", "source_invalidated"})


class CognitiveStorageLane:
    def __init__(self, store):
        self.store = store
        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="amora-cognitive-db")
        self.lock = asyncio.Lock()
        self.compile_lock = asyncio.Lock()
        self.worker = None
        self.closing = False

    def _execute(self, method, args, kwargs):
        if self.worker is None:
            self.worker = type(self.store)(self.store.database_path, self.store.clock,
                                           backend=self.store.backend)
        return getattr(self.worker, method)(*args, **kwargs)

    async def _join(self, future):
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(future)
                break
            except asyncio.CancelledError:
                cancelled = True
                if future.cancelled():
                    raise
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def call(self, method, *args, **kwargs):
        if method not in _ALLOWED:
            raise ValueError("COGNITIVE_STORAGE_LANE_METHOD_NOT_ALLOWED")
        if asyncio.get_running_loop() is not self.loop or self.closing:
            raise RuntimeError("COGNITIVE_STORAGE_LANE_UNAVAILABLE")
        with span("cognitive_db_lane_wait"):
            await self.lock.acquire()
        try:
            if self.closing:
                raise RuntimeError("COGNITIVE_STORAGE_LANE_CLOSING")
            context = copy_context()
            with span("cognitive_db_lane"):
                return await self._join(self.loop.run_in_executor(
                    self.executor, context.run, self._execute, method, args, kwargs))
        finally:
            self.lock.release()

    def _close(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None

    async def close(self):
        self.closing = True
        async with self.lock:
            try:
                await self._join(self.loop.run_in_executor(self.executor, self._close))
            finally:
                self.executor.shutdown(wait=True)
