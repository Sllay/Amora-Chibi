"""Clock, history and unsolicited delivery regression tests (no live provider)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.autonomy.models import DesireKind, Intention
from amora.cognition.models import CognitiveRequest, ConversationMessage, MessageSource, MessageStatus
from amora.cognition.prompt_frame import turn_frame, render_current_turn
from amora.cognition.temporal import opportunity_state, trusted_opportunity
from amora.messaging.models import ChannelType, MessagingPlatform, PresenceCommand, PresenceState
from amora.messaging.temporal_initiative import TemporalInitiative
from amora.time import SimulatedClock
from test_messaging_lifecycle_stability import CountingStructuredProvider
from test_platform_messaging_step2 import _bind_bk, _message
from test_persistent_owner_recall import _memory


class TemporalProvider(CountingStructuredProvider):
    def __init__(self, silent=False):
        super().__init__(name="temporal-test")
        self.contexts = []
        self.silent = silent

    async def realize(self, context):
        self.calls += 1
        self.contexts.append(context)
        if context.temporal_opportunity:
            if self.silent:
                moves = [{"kind": "stay_silent", "text": "SILENCE"}]
            else:
                moves = [{"kind": context.temporal_opportunity["allowed_actions"][0],
                          "text": "E aí, conseguiu decidir?"}]
        else:
            moves = [{"kind": context.dialogue_plan["response_moves"][0],
                      "text": "E o seu projeto, como ficou?"}]
        return {"moves": moves, "closing": "none" if context.temporal_opportunity and self.silent else "statement",
                "_usage": {"input_tokens": 12, "output_tokens": 8, "status_code": 200}}


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_CONVERSATION_TIMEZONE", "America/Recife")
    monkeypatch.setenv("AMORA_TEMPORAL_INITIATIVE", "1")
    monkeypatch.setenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", "0")
    core = AmoraCore.bootstrap(clock=SimulatedClock(datetime(2026, 11, 16, 16, 53, tzinfo=timezone.utc)))
    yield core
    core.close()


def test_clock_and_six_authorized_messages(core):
    now = core.clock.now()
    ids = []
    for n in range(8):
        turn = uuid4()
        ids.append(str(turn))
        core.cognition.store.put_message(ConversationMessage(
            turn_id=turn, speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA",
            source=MessageSource.TEXT, status=MessageStatus.RECEIVED,
            raw_text=f"mensagem {n}", session_id="temporal-session",
            started_at=now - timedelta(minutes=8-n), remote_processing_allowed=True,
        ))
    core.cognition.store.put_message(ConversationMessage(
        speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA", source=MessageSource.TEXT,
        status=MessageStatus.RECEIVED, raw_text="outro canal secreto", session_id="other-session",
        started_at=now - timedelta(seconds=10), remote_processing_allowed=True,
    ))
    context = core.cognition.compiler.compile(CognitiveRequest(
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA", text="Que horas são?",
        session_id="temporal-session", remote_processing_allowed=True,
    ))
    temporal = context.temporal_context
    assert temporal["now_local"] == "2026-11-16T13:53:00-03:00"
    assert temporal["weekday_pt_br"] == "segunda-feira"
    assert [item["turn_id"] for item in temporal["recent_user_messages"]] == ids[-6:]
    assert temporal["seconds_since_previous_user_message"] == 60
    assert turn_frame(context)["temporal_context"] == temporal
    assert "outro canal secreto" not in str(context.model_dump())


@pytest.mark.parametrize("minutes,text,state", [
    (2, "Me responde logo?", "quick_answer_pending"),
    (5, "Como ficou?", "question_pending"),
    (45, "Que legal.", "conversation_quiet"),
    (360, "Que legal.", "long_absence"),
])
def test_opportunity_states(minutes, text, state, monkeypatch):
    monkeypatch.setenv("AMORA_CONVERSATION_TIMEZONE", "America/Recife")
    now = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)
    then = now - timedelta(minutes=minutes)
    op = opportunity_state(now, then, then, text)
    assert op["state"] == state
    assert "stay_silent" in op["allowed_actions"]
    assert trusted_opportunity(op)["state"] == state
    assert trusted_opportunity({"state": []}) is None
    assert opportunity_state(now.replace(hour=3), then, then, text) is None


def connected(core):
    platform = MessagingPlatform.DISCORD
    p = core.platform_presence.request(platform, PresenceCommand.CONNECT)
    core.platform_presence.report(platform, PresenceState.CONNECTING, generation=p.generation)
    core.platform_presence.report(platform, PresenceState.CONNECTED, generation=p.generation)


async def inbound(core, mid="temporal-1"):
    return await core.messaging.receive(_message(
        core, message_id=mid, actor_id="111111111111111111", channel_type=ChannelType.DM,
        text="Estou fazendo um projeto novo hoje.", conversation_id="temporal-dm",
    ).model_copy(update={"remote_processing_allowed": True}))


def deliver(core, out):
    claimed = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert out.id in {item.id for item in claimed}
    assert core.messaging_store.ack_outbound(out.id, sent=True, external_message_id="sent-" + str(out.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("silent", [False, True])
async def test_real_initiative_pipeline_and_memory(core, silent):
    _bind_bk(core)
    connected(core)
    provider = TemporalProvider(silent=silent)
    core.cognition.register_provider(provider)
    remembered = _memory(core, "Projeto temporal: mapa de uma ilha.")
    result = await inbound(core)
    assert len(result.outbound) == 1
    original = result.outbound[0]
    deliver(core, original)
    core.clock.advance(timedelta(minutes=6))
    initiative = TemporalInitiative(core)
    out = await initiative.tick()
    ctx = provider.contexts[-1]
    assert ctx.temporal_opportunity["state"] == "question_pending"
    assert ctx.temporal_opportunity["seconds_since_amora"] == 360
    assert "core_temporal_initiative" in render_current_turn(ctx)
    mem = next(item for item in ctx.memories if item.id == str(remembered.id))
    assert mem.recorded_at == remembered.created_at.isoformat()
    assert not [item for item in core.cognition.store.list_messages(turn_id=ctx.turn_id)
                if item.speaker_id == "PERSON_BK"]
    if silent:
        assert out is None
        assert not core.cognition.store.list_messages(turn_id=ctx.turn_id)
    else:
        assert out is not None and out.reply_to_message_id is None
        assert out.metadata["temporal_initiative"] is True
        assert not core.cognition.store.list_messages(turn_id=out.id, exclude_invalidated=True)
        deliver(core, out)
        assert core.cognition.store.list_messages(turn_id=out.id, exclude_invalidated=True)
    assert await initiative.tick() is None


@pytest.mark.asyncio
async def test_new_user_message_cancels_queued_initiative(core):
    _bind_bk(core)
    connected(core)
    core.cognition.register_provider(TemporalProvider())
    result = await inbound(core)
    deliver(core, result.outbound[0])
    core.clock.advance(timedelta(minutes=6))
    initiative = TemporalInitiative(core)
    out = await initiative.tick()
    assert out is not None
    core.clock.advance(timedelta(seconds=1))
    new = await inbound(core, "temporal-2")
    claimed = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
    assert out.id not in {item.id for item in claimed}
    assert new.outbound[0].id in {item.id for item in claimed}


def test_bdi_opportunity_is_trusted_and_non_compulsory(monkeypatch):
    monkeypatch.setenv("AMORA_CONVERSATION_TIMEZONE", "America/Recife")
    now = datetime(2026, 11, 16, 18, tzinfo=timezone.utc)
    then = now - timedelta(minutes=10)
    base = {"status": "active", "kind": "initiate_contact",
            "target_id": "PERSON_BK", "selected_utility": 0.78}
    assert opportunity_state(now, then, then, "Conversa encerrada.") is None
    decision = opportunity_state(now, then, then, "Conversa encerrada.", intention=base)
    assert decision["state"] == "motivated_contact"
    assert "stay_silent" in decision["allowed_actions"]
    assert trusted_opportunity(decision)["state"] == "motivated_contact"
    assert opportunity_state(now, then, then, "Conversa encerrada.",
                             intention={**base, "target_id": "OTHER"}) is None
    assert opportunity_state(now, then, then, "Conversa encerrada.",
                             intention={**base, "selected_utility": 0.40}) is None


@pytest.mark.asyncio
async def test_core_bdi_intention_opens_authorized_owner_dm(core):
    _bind_bk(core)
    connected(core)
    class StatementProvider(TemporalProvider):
        async def realize(self, context):
            if context.temporal_opportunity:
                return await super().realize(context)
            self.calls += 1
            self.contexts.append(context)
            return {"moves": [{"kind": context.dialogue_plan["response_moves"][0],
                               "text": "Legal, teu projeto tá encaminhado."}],
                    "closing": "statement",
                    "_usage": {"input_tokens": 12, "output_tokens": 8, "status_code": 200}}

    provider = StatementProvider()
    core.cognition.register_provider(provider)
    result = await inbound(core)
    deliver(core, result.outbound[0])
    core.clock.advance(timedelta(minutes=10))
    now = core.clock.now()
    core.autonomy.store.put_intention(Intention(
        kind=DesireKind.INITIATE_CONTACT, target_id="PERSON_BK",
        source_kind="drive", selected_utility=0.78,
        urgency=0.7, created_at=now, updated_at=now,
        expires_at=now + timedelta(minutes=25),
    ))
    initiative = TemporalInitiative(core)
    outbound = await initiative.tick()
    assert outbound is not None
    assert outbound.metadata["initiative_state"] == "motivated_contact"
    assert provider.contexts[-1].temporal_opportunity["state"] == "motivated_contact"
    assert await initiative.tick() is None
    deliver(core, outbound)
