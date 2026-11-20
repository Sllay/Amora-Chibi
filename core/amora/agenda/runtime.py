"""Core-owned agenda lifecycle; existing Scheduler remains the timer."""
import asyncio
import logging
import os

from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.messaging.session_identity import messaging_session_ref

from .service import AgendaService

_LOG = logging.getLogger("amora.agenda")
JOB_ID = "agenda.owner_occurrences"


class AgendaRunner:
    def __init__(self, core):
        self.core = core
        self.service = AgendaService(core.database, core.clock, core.messaging_store)
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
            rendered = {}
            # Compose only active, verified, unsent occurrences. The ledger and
            # claim-time guard still decide whether a message can be delivered.
            due = await asyncio.to_thread(self.service.due_items)
            for item, when, oid in due[:3]:
                text = await self._compose(item, when, oid)
                if text:
                    rendered[str(oid)] = text
            await asyncio.to_thread(self.service.tick, rendered=rendered)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("agenda scan failed: %s", type(exc).__name__)

    async def _compose(self, item, when, oid):
        """Let cognition phrase an explicit reminder, never create its schedule.

        Only the original, consented owner-DM instruction may be projected to
        a remote model. Operator-created tasks without that evidence retain
        their exact original text rather than silently extending consent.
        """
        if os.getenv("AMORA_AGENDA_COGNITIVE_COMPOSE", "1").strip().lower() in {
            "0", "false", "off", "no"
        }:
            return None
        session = messaging_session_ref("discord", item.account_scope, item.conversation_id)
        source = self.core.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{item.request_id}"
        )
        if (source is None or source.speaker_id != "PERSON_BK"
                or source.session_id != session or not source.remote_processing_allowed
                or source.metadata.get("test_mode")
                or not source.metadata.get("agenda_owner_command")
                or source.metadata.get("channel_type") != "dm"
                or source.metadata.get("platform") != "discord"):
            return None
        request = CognitiveRequest(
            text=item.text,
            speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id=session, turn_id=oid,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={
                "surface": "platform_messaging", "platform": "discord",
                "channel_type": "dm", "cognitive_mode": "scheduled_reminder",
                "temporal_initiative": True,  # ACK-gated history; not a fake user turn
                "agenda_due_at": when.isoformat(), "agenda_timezone": item.timezone,
                "memory_consolidation": False, "relationship_learning": False,
                "provider_total_budget_seconds": 85.0,
                "provider_attempt_budget_seconds": 75.0,
            },
        )
        try:
            plan = await asyncio.wait_for(self.core.cognition.respond(request), timeout=100)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("agenda composition unavailable: %s", type(exc).__name__)
            return None
        text = plan.display_text.strip()
        if (plan.fallback_used or not plan.provider
                or not plan.metadata.get("realization_validated")
                or not 0 < len(text) <= 360):
            return None
        return text

    async def stop(self):
        self.core.scheduler.cancel(JOB_ID)
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
