from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

from .service import Scheduler


class SchedulerRuntime:
    """Async lifecycle runner for the deterministic Scheduler.

    The Scheduler itself stays clock-driven and test-friendly. This runner only
    provides the production event-loop lifecycle: poll due jobs, respect the
    caller's idle predicate, and stop cleanly during application shutdown.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        *,
        is_idle: Callable[[], bool],
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.scheduler = scheduler
        self.is_idle = is_idle
        self.poll_interval_seconds = float(poll_interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self) -> list[str]:
        return await self.scheduler.run_due(is_idle=bool(self.is_idle()))

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run_forever(), name="amora-scheduler-runtime")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self.poll_interval_seconds)
