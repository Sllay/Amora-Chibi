"""Run 1: historical conversation recall is temporal, source-bound and private."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone, timedelta
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition.models import ConversationMessage, MessageSource, MessageStatus
from amora.events import PrivacyScope
from amora.memory.temporal_recall import parse_temporal_recall
from amora.messaging.models import OutboundMessage, MessagingPlatform, OutboundStatus
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk
from test_persistent_owner_recall import _dm
from test_owner_memory_disclosure import _group

TZ = timezone(timedelta(hours=-3))
ON_DAY = datetime(2026, 11, 11, 14, 15, tzinfo=TZ)
SOURCE = "O projeto Orquídea terá cinco mapas e criaturas marinhas."
ANSWER = "Concordei que seria melhor começar pelo mapa da praia."
PRIVATE = "SEGREDO_NAO_RELACIONADO"


@pytest.mark.parametrize("utterance, day, topic, own_reply", [
    ("Lembra do projeto que te falei dia 11/11?", date(2026, 11, 11), set(), False),
    ("Amora, lembra do projeto Orquídea que te falei dia 11/11/2026?",
     date(2026, 11, 11), {"orquidea"}, False),
    ("O que eu te falei em 11/11/2026?", date(2026, 11, 11), set(), False),
    ("O que você me respondeu dia 11/11/2026?", date(2026, 11, 11), set(), True),
    ("O que conversamos no dia 11/11?", date(2026, 11, 11), set(), False),
])
def test_interpret_owner_date_without_llm(utterance, day, topic, own_reply):
    now = datetime(2026, 11, 19, 22, tzinfo=TZ)
    intent = parse_temporal_recall(utterance, now)
    assert intent is not None and intent.day == day
    assert intent.topic == topic
    assert intent.assistant_only == own_reply
    lo, hi = intent.utc_bounds()
    assert (hi - lo) == timedelta(days=1)
    assert lo == datetime(2026, 11, 11, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize("utterance", [
    'Ela disse: "Lembra do projeto no dia 11/11?"',
    "Não quero que você lembre do dia 11/11.",
    "Lembre que meu projeto começou dia 11/11.",
    "Lembra de 31/11/2026?",
    "Lembra de 29/02/2025?",
    "> Lembra do que eu falei dia 11/11?",
    "Lembra de 11/11 e 12/11?",
])
def test_quoted_negated_invalid_or_competing_dates_are_not_commands(utterance):
    assert parse_temporal_recall(utterance, datetime(2026, 11, 19, tzinfo=timezone.utc)) is None


def test_no_year_refers_to_latest_past_occurrence():
    intent = parse_temporal_recall(
        "Lembra do que conversamos dia 25/12?",
        datetime(2026, 11, 19, tzinfo=timezone.utc),
    )
    assert intent and intent.day == date(2025, 12, 25)


def insert_turn(core, *, at=ON_DAY, content=SOURCE, reply=ANSWER,
                owner="PERSON_BK", channel="owner-dm", status=OutboundStatus.SENT,
                invalidated=False):
    turn = uuid4()
    session_id = core.messaging._session_ref(_dm(core, f"source-{turn}", "oi", conversation=channel))
    native_id = f"source-{turn}"
    native_hash = hashlib.sha256(native_id.encode()).hexdigest()[:20]
    item = ConversationMessage(
        turn_id=turn, speaker_id=owner, addressee_id="ENTITY_AMORA",
        source=MessageSource.TEXT, status=MessageStatus.RECEIVED,
        raw_text=content, started_at=at.astimezone(timezone.utc),
        session_id=session_id, privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={
            "surface": "platform_messaging", "channel_type": "dm", "platform": "discord",
            "platform_sent_at": at.isoformat(), "native_message_id_hash": native_hash,
            "transport_revision": 1,
        },
        dedupe_key=f"cognitive-input:{turn}",
    )
    assert core.cognition.store.put_message(item)
    if reply is not None:
        outgoing = OutboundMessage(
            platform=MessagingPlatform.DISCORD, account_scope="primary",
            conversation_id=channel, target_entity_id=owner, text=reply,
            source_message_id=native_id, created_at=at + timedelta(seconds=45),
            status=status, metadata={"cognitive_turn_id": str(turn)},
        )
        assert core.messaging.store.put_outbound(outgoing)
    if invalidated:
        core.messaging.reflections.invalidate_source(session_id, native_hash, through_revision=1)
    return turn


@pytest.mark.asyncio
async def test_local_calendar_recall_finds_actual_reply_after_restart_and_uses_no_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    first = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(first)
        insert_turn(first)
        insert_turn(first, at=ON_DAY + timedelta(days=1), content=PRIVATE, reply="PRIVATE_REPLY")
        insert_turn(first, at=ON_DAY, content="Outro usuário escreveu segredo.", owner="OTHER_USER",
                    channel="foreign-dm", reply="FOREIGN_REPLY")
        insert_turn(first, at=ON_DAY, content="Conteúdo que foi editado.", invalidated=True,
                    reply="CANCELLED_REPLY")
        insert_turn(first, at=ON_DAY, content="Rascunho que nunca enviei.",
                    reply="UNSENT_REPLY", status=OutboundStatus.QUEUED)
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(second)
        async def forbidden(*a, **k):
            pytest.fail("date recall must not send historical content to a model")
        monkeypatch.setattr(second.cognition, "respond", forbidden)
        monkeypatch.setattr(second.cognition, "reflect", forbidden)
        prompt = _dm(second, "date-recall", "Lembra do projeto Orquídea que te falei dia 11/11/2026?")
        result = await second.messaging.receive(prompt)
        assert len(result.outbound) == 1
        outbound = result.outbound[0]
        assert outbound.metadata["cognitive_reason_codes"] == ["TEMPORAL_RECALL_FOUND"]
        assert SOURCE in outbound.text and ANSWER in outbound.text
        assert "14:15" in outbound.text and "11/11/2026" in outbound.text
        assert all(s not in outbound.text for s in (
            PRIVATE, "FOREIGN_REPLY", "UNSENT_REPLY", "CANCELLED_REPLY",
        ))
        assert outbound.metadata["memory_recall_source"] == "verified_conversation_history"
        assert second.messaging.reflections.state(second.messaging._turn_id(prompt)) is None
    finally:
        second.close()


@pytest.mark.asyncio
async def test_only_sent_reply_is_quoted(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        insert_turn(core, content="Nosso coelho vai ter bigodes?", reply="NUNCA_ENVIADO",
                    status=OutboundStatus.CANCELLED)
        query = _dm(core, "date-reply", "O que você me respondeu dia 11/11/2026?")
        reply = (await core.messaging.receive(query)).outbound[0]
        assert "Nosso coelho" in reply.text
        assert "NUNCA_ENVIADO" not in reply.text
        assert "Não encontrei uma resposta confirmada como enviada" in reply.text
    finally:
        core.close()


@pytest.mark.asyncio
async def test_group_date_query_never_discloses_private_history(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        insert_turn(core)
        group = _group(core, "date-lobby", "Lembra do projeto que te falei dia 11/11/2026?",
                       channel="lobby")
        result = await core.messaging.receive(group)
        assert len(result.outbound) == 1
        response = result.outbound[0]
        assert response.metadata["cognitive_reason_codes"] == ["TEMPORAL_RECALL_PRIVATE_DM_REQUIRED"]
        assert SOURCE not in response.text and ANSWER not in response.text
        assert response.metadata["memory_recall_count"] == 0
    finally:
        core.close()
