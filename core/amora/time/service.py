from __future__ import annotations

import heapq
import inspect
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class SimulatedClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime.now(timezone.utc)
        if self._now.tzinfo is None or self._now.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, delta: timedelta) -> None:
        seconds = delta.total_seconds()
        if seconds < 0:
            raise ValueError("simulated clock cannot move backwards")
        self._now += delta
        self._mono += seconds


JobCallback = Callable[[], object | Awaitable[object]]


@dataclass(order=True, slots=True)
class _ScheduledJob:
    due: float
    order: int
    job_id: str = field(compare=False)
    callback: JobCallback = field(compare=False)
    interval: float | None = field(default=None, compare=False)
    idle_only: bool = field(default=False, compare=False)
    cancelled: bool = field(default=False, compare=False)


class Scheduler:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self._heap: list[_ScheduledJob] = []
        self._jobs: dict[str, _ScheduledJob] = {}
        self._counter = itertools.count()

    def schedule_once(self, job_id: str, delay_seconds: float, callback: JobCallback) -> None:
        self._schedule(job_id, delay_seconds, callback, interval=None, idle_only=False)

    def schedule_interval(self, job_id: str, interval_seconds: float, callback: JobCallback) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._schedule(job_id, interval_seconds, callback, interval=interval_seconds, idle_only=False)

    def schedule_idle(self, job_id: str, delay_seconds: float, callback: JobCallback) -> None:
        self._schedule(job_id, delay_seconds, callback, interval=None, idle_only=True)

    def schedule_idle_interval(self, job_id: str, interval_seconds: float, callback: JobCallback) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._schedule(job_id, interval_seconds, callback, interval=interval_seconds, idle_only=True)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        job.cancelled = True
        return True

    async def run_due(self, *, is_idle: bool = True) -> list[str]:
        executed: list[str] = []
        now = self.clock.monotonic()
        while self._heap and self._heap[0].due <= now:
            job = heapq.heappop(self._heap)
            if job.cancelled or self._jobs.get(job.job_id) is not job:
                continue
            if job.idle_only and not is_idle:
                job.due = now + 1.0
                heapq.heappush(self._heap, job)
                continue
            result = job.callback()
            if inspect.isawaitable(result):
                await result
            executed.append(job.job_id)
            if job.interval is None:
                # The callback may intentionally replace this one-shot job with
                # another job using the same ID. Only remove the ID if it still
                # points at the job that just ran.
                if self._jobs.get(job.job_id) is job:
                    self._jobs.pop(job.job_id, None)
            else:
                job.due = now + job.interval
                job.order = next(self._counter)
                heapq.heappush(self._heap, job)
        return executed

    def snapshot(self) -> list[dict[str, object]]:
        now = self.clock.monotonic()
        return [{"job_id": job.job_id, "state": "scheduled", "due_in_seconds": max(0.,job.due-now),
                 "idle_only": job.idle_only, "interval_seconds": job.interval}
                for job in self._jobs.values() if not job.cancelled]

    def _schedule(self, job_id: str, delay_seconds: float, callback: JobCallback, *, interval: float | None, idle_only: bool) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self.cancel(job_id)
        job = _ScheduledJob(self.clock.monotonic() + delay_seconds, next(self._counter), job_id, callback, interval, idle_only)
        self._jobs[job_id] = job
        heapq.heappush(self._heap, job)
