from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

from .service import Scheduler


class SchedulerRuntime:
    """Async lifecycle runner for the deterministic Scheduler.

    The Scheduler itself stays clock-driven and test-friendly. This runner only
    provides the production event-loop lifecycle: poll due jobs, respect the
    caller's idle predicate, keep running after a job failure, and stop cleanly
    during application shutdown.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        *,
        is_idle: Callable[[], bool],
        poll_interval_seconds: float = 0.5,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.scheduler = scheduler
        self.is_idle = is_idle
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.on_error = on_error
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
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        # Jobs are in-memory runtime state. Once the lifecycle runner stops,
        # leaving them armed can resurrect callbacks if the same Core object is
        # re-used by a host/test. Cancel after the task is joined so a callback
        # cancelled mid-flight cannot re-arm itself in a finally block.
        for job in self.scheduler.snapshot():
            self.scheduler.cancel(str(job["job_id"]))

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(exc)
            await asyncio.sleep(self.poll_interval_seconds)
