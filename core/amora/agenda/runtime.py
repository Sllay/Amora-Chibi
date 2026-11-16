"""Core-owned agenda lifecycle; existing Scheduler remains the timer."""
import asyncio
import logging

from .service import AgendaService

_LOG = logging.getLogger("amora.agenda")
JOB_ID = "agenda.owner_occurrences"


class AgendaRunner:
    def __init__(self, core):
        self.core = core
        self.service = AgendaService(core)
        self.task = None

    def start(self):
        # Unlike idle-only social initiative, explicit appointments must run
        # even while the user is speaking.
        self.core.scheduler.schedule_interval(JOB_ID, 30, self._trigger)

    def _trigger(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._scan(), name=JOB_ID)

    async def _scan(self):
        try:
            await asyncio.to_thread(self.service.tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("agenda scan failed: %s", type(exc).__name__)

    async def stop(self):
        self.core.scheduler.cancel(JOB_ID)
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
