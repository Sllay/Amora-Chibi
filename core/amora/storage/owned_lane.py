"""Bounded storage executor shared by explicitly owned remote DB facades."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from amora.latency_trace import span

class OwnedStorageLane:
    """One connection owner, one operation at a time; join before cancellation."""

    allowed = frozenset()
    trace_name = "owned_db_lane"
    def __init__(self, *, thread_name):
        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name)
        self.lock = asyncio.Lock()
        self.worker = None
        self.closing = False

    def _make_worker(self):
        raise NotImplementedError

    def _execute(self, method, args, kwargs):
        if self.worker is None:
            self.worker = self._make_worker()
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
        if method not in self.allowed:
            raise ValueError("OWNED_STORAGE_LANE_METHOD_NOT_ALLOWED")
        if asyncio.get_running_loop() is not self.loop or self.closing:
            raise RuntimeError("OWNED_STORAGE_LANE_UNAVAILABLE")
        with span(self.trace_name + "_wait"):
            await self.lock.acquire()
        try:
            if self.closing:
                raise RuntimeError("OWNED_STORAGE_LANE_CLOSING")
            with span(self.trace_name):
                context = copy_context()
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
