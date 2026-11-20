"""Scheduled reminders: cognitive wording is separate from the owner's due obligation."""
from datetime import timedelta

import pytest

from amora.agenda import AgendaService
from amora.agenda.runtime import AgendaRunner
from amora.agenda.service import agenda_claim_allowed
from amora.cognition.prompt_frame import render_current_turn
from amora.messaging import ChannelType, MessagingPlatform
from test_agenda_durable_v1 import core, connected, START, EXTERNAL
from test_platform_messaging_step2 import _message
from test_messaging_lifecycle_stability import CountingStructuredProvider


class ReminderProvider(CountingStructuredProvider):
    def __init__(self):
        super().__init__(name="agenda-composition-fixture")
        self.contexts = []

    async def realize(self, context):
        self.calls += 1
        self.contexts.append(context)
        return {
            "moves": [{"kind": "deliver_reminder",
                       "text": "Ei, lembra de cobrar teu amigo pela camisa hoje, viu!"}],
            "closing": "statement",
            "_usage": {"input_tokens": 15, "output_tokens": 18, "status_code": 200},
        }


@pytest.mark.asyncio
async def test_cognitive_reminder_is_own_voice_and_claim_bound(core):
    connected(core)
    provider = ReminderProvider()
    core.cognition.register_provider(provider)
    result = await core.messaging.receive(_message(
        core, message_id="agenda-compose-1", actor_id=EXTERNAL,
        channel_type=ChannelType.DM, conversation_id="agenda-dm",
        text="Amora me lembra hoje às 15:30 de cobrar meu amigo pela camisa",
    ).model_copy(update={"remote_processing_allowed": True}))
    assert result.outbound
    pending = core.messaging_store.claim_outbound(
        platform=MessagingPlatform.DISCORD, account_scope="primary")
    for reply in pending:
        assert core.messaging_store.ack_outbound(
            reply.id, sent=True, external_message_id="reply-" + str(reply.id))

    service = AgendaService(core.database, core.clock, core.messaging_store)
    [item] = service.list()
    assert item.text == "cobrar meu amigo pela camisa"
    core.clock.advance(timedelta(minutes=10))
    runner = AgendaRunner(core)
    await runner._scan()
    [pending_reminder] = core.messaging_store.claim_outbound(
        platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert "Ei, lembra" in pending_reminder.text
    assert pending_reminder.text != item.text
    assert pending_reminder.metadata["agenda_render_kind"] == "cognitive"
    assert agenda_claim_allowed(core.database._conn, pending_reminder, core.clock.now())
    assert "SCHEDULED_REMINDER" in provider.contexts[-1].reason_codes
    assert "core_scheduled_reminder" in render_current_turn(provider.contexts[-1])
    assert not [m for m in core.cognition.store.list_messages(
        turn_id=pending_reminder.id) if m.speaker_id == "PERSON_BK"]
    assert core.messaging_store.ack_outbound(
        pending_reminder.id, sent=True, external_message_id="reminder-sent")
    assert service.tick() == []


@pytest.mark.asyncio
async def test_no_source_consent_never_calls_provider(core):
    connected(core)
    provider = ReminderProvider()
    core.cognition.register_provider(provider)
    await core.messaging.receive(_message(
        core, message_id="agenda-compose-private", actor_id=EXTERNAL,
        channel_type=ChannelType.DM, conversation_id="agenda-dm",
        text="Amora me lembra hoje às 15:30 de cobrar meu amigo pela camisa",
    ))
    for reply in core.messaging_store.claim_outbound(
        platform=MessagingPlatform.DISCORD, account_scope="primary"
    ):
        core.messaging_store.ack_outbound(
            reply.id, sent=True, external_message_id="reply-" + str(reply.id))
    [item] = AgendaService(core.database, core.clock, core.messaging_store).list()
    core.clock.advance(timedelta(minutes=10))
    await AgendaRunner(core)._scan()
    [outbound] = core.messaging_store.claim_outbound(
        platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert outbound.text == item.text
    assert not provider.contexts


@pytest.mark.asyncio
async def test_composed_reminder_hash_is_checked_at_claim(core):
    connected(core)
    await core.messaging.receive(_message(
        core, message_id="agenda-compose-hash", actor_id=EXTERNAL,
        channel_type=ChannelType.DM, conversation_id="agenda-dm",
        text="Amora me lembra hoje às 15:30 de cobrar meu amigo pela camisa",
    ))
    for reply in core.messaging_store.claim_outbound(
        platform=MessagingPlatform.DISCORD, account_scope="primary"):
        core.messaging_store.ack_outbound(reply.id, sent=True, external_message_id="reply-hash")
    service = AgendaService(core.database, core.clock, core.messaging_store)
    core.clock.advance(timedelta(minutes=10))
    [(item, when, oid)] = service.due_items()
    [outbound] = service.tick(rendered={str(oid): "Ei, cobra teu amigo pela camisa!"})
    assert agenda_claim_allowed(core.database._conn, outbound, core.clock.now())
    tampered = outbound.model_copy(update={"text": "Mensagem alterada"})
    assert not agenda_claim_allowed(core.database._conn, tampered, core.clock.now())
