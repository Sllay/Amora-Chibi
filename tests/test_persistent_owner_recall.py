from __future__ import annotations

import json

import pytest

from amora.admin_commands import _command_memory_recall_inspect
from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind
from amora.memory.recall_intent import parse_recall_intent, topic_terms
from amora.messaging import ChannelType
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk, _message

OWNER = "111111111111111111"
SOURCE = "Meu projeto Orquídea terá cinco mapas e será de sobrevivência."
SECRET_MARKER = "UNRELATED_PRIVATE_MARKER"


@pytest.mark.parametrize("text, topic, generic", [
    ("Amora, o que você lembra sobre o meu projeto Orquídea?", "meu projeto orquidea", False),
    ("Vc se lembra do projeto Orquídea?", "projeto orquidea", False),
    ("Tu lembra do que eu falei sobre Orquídea?", "orquidea", False),
    ("Oque estávamos conversando um tempo atrás?", "", True),
    ("O que você lembra de mim?", "", True),
])
def test_recall_intent(text, topic, generic):
    intent = parse_recall_intent(text)
    assert intent and intent.topic == topic and intent.generic is generic


@pytest.mark.parametrize("text", [
    "Lembre que meu jogo terá cinco mapas.",
    "Não quero que você lembre do projeto.",
    "Ela disse que lembra do projeto.",
    "Meu projeto chama Orquídea.",
])
def test_normal_chat_is_not_persistent_recall(text):
    assert parse_recall_intent(text) is None


def _dm(core, mid, text, *, conversation="owner-dm", remote=True):
    return _message(core, message_id=mid, actor_id=OWNER, channel_type=ChannelType.DM,
                    text=text, conversation_id=conversation).model_copy(
                        update={"remote_processing_allowed": remote})


def _memory(core, content, *, owner="PERSON_BK", scope=PrivacyScope.PERSONAL,
            remote=True, summary=None):
    result = core.memory.remember(MemoryCandidate(
        kind=MemoryKind.EPISODIC, content=content, summary=summary,
        provenance=MemoryProvenance(kind=ProvenanceKind.USER_ASSERTION,
                                    source="test", actor_id=owner),
        owner_entity_id=owner, privacy_scope=scope, remote_processing_allowed=remote,
        entity_refs={owner: 1.0}, importance=1, novelty=1, future_consequence=1,
    ))
    assert result.memory
    return result.memory


@pytest.mark.asyncio
async def test_persistent_recall_from_actual_save_after_restart_without_history(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_REFLECTION_REMOTE_REUSE", "1")
    clock = SimulatedClock()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        _bind_bk(first)
        original = _dm(first, "save-orquidea", SOURCE + " Quero que você lembre disso.")
        saved = (await first.messaging.receive(original)).outbound[0]
        mid = saved.metadata["explicit_memory_id"]
        turn = first.messaging._turn_id(original)
        _memory(first, SECRET_MARKER)
        _memory(first, "Projeto Orquídea com outro segredo", owner="ENTITY_CHATGPT")
    finally:
        first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        async def no_provider(*a, **kw):
            pytest.fail("recall must not require a provider or recent history")
        monkeypatch.setattr(second.cognition, "respond", no_provider)
        monkeypatch.setattr(second.cognition, "reflect", no_provider)
        request = _dm(second, "recall-orquidea", "Amora, lembra do projeto Orquidea?",
                      conversation="synthetic-new-private-session")
        result = await second.messaging.receive(request)
        out = result.outbound[0]
        assert out.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_PERSISTENT_FOUND"]
        assert out.metadata["memory_recall_ids"] == [mid]
        assert "cinco mapas" in out.text and "sobrevivência" in out.text
        assert SECRET_MARKER not in out.text and "outro segredo" not in out.text
        assert second.messaging.reflections.state(second.messaging._turn_id(request)) is None
        assert second.memory.get(mid).access_count == 0
        audit = await _command_memory_recall_inspect(second, [str(turn), "Orquidea"])
        assert audit["data"]["record_count"] == 1
        assert audit["data"]["returned_from_turn_count"] == 1
        assert audit["data"]["records"][0]["remote_processing_allowed"] is True
        assert SOURCE not in json.dumps(audit, ensure_ascii=False)
        assert "cinco mapas" not in json.dumps(audit, ensure_ascii=False)
    finally:
        second.close()


@pytest.mark.asyncio
async def test_group_does_not_confirm_private_record_existence_or_leak_content(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        private = _memory(core, SOURCE, remote=False)
        async def no_provider(*a, **kw):
            pytest.fail("group recall restriction must be local")
        monkeypatch.setattr(core.cognition, "respond", no_provider)
        def group(mid, text):
            return _message(core, message_id=mid, actor_id=OWNER, channel_type=ChannelType.GROUP,
                            mention=True, text=text).model_copy(update={"remote_processing_allowed": True})
        a = (await core.messaging.receive(group("g1", "Amora, lembra do projeto Orquídea?"))).outbound[0]
        b = (await core.messaging.receive(group("g2", "Amora, lembra do projeto Inexistente?"))).outbound[0]
        assert a.text == b.text
        assert a.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_SHARED_SCOPE_RESTRICTED"]
        assert a.metadata["memory_recall_ids"] == [] and b.metadata["memory_recall_ids"] == []
        assert str(private.id) not in a.model_dump_json() and "cinco mapas" not in a.text
        public = _memory(core, "Projeto Público Azul usa três mapas.", scope=PrivacyScope.PUBLIC)
        c = (await core.messaging.receive(group("g3", "Amora, lembra do projeto Azul?"))).outbound[0]
        assert c.metadata["memory_recall_ids"] == [str(public.id)]
        assert c.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_PUBLIC_FOUND"]
        assert "três mapas" in c.text and "cinco mapas" not in c.text
    finally:
        core.close()


@pytest.mark.asyncio
async def test_local_only_owner_recall_does_not_promote_remote_and_no_match_is_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        local = _memory(core, SOURCE, scope=PrivacyScope.PRIVATE, remote=False)
        _memory(core, SECRET_MARKER)
        out = (await core.messaging.receive(_dm(core, "local", "Lembra do projeto Orquídea?"))).outbound[0]
        assert out.metadata["memory_recall_ids"] == [str(local.id)]
        assert "cinco mapas" in out.text
        assert not core.memory.get(local.id).remote_processing_allowed
        miss = (await core.messaging.receive(_dm(core, "miss", "Lembra do projeto Inexistente?"))).outbound[0]
        assert miss.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_NO_MATCH"]
        assert SECRET_MARKER not in miss.text and "cinco mapas" not in miss.text
        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text="Lembra do projeto Orquídea?", speaker_id="PERSON_BK", remote_processing_allowed=True,
            session_id="other-session"))
        assert str(local.id) not in {item.id for item in ctx.memories}
        assert SECRET_MARKER not in " ".join(item.content for item in ctx.memories)
    finally:
        core.close()


def test_non_fts_recall_is_accent_insensitive_and_excludes_archived_and_unrelated(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        core.memory.store.fts_enabled = False
        active = _memory(core, SOURCE)
        archived = _memory(core, "Orquídea versão descartada")
        core.memory.archive(archived.id)
        _memory(core, "Projeto Aurora")
        matches, complete = core.memory.store.recall_owner_records("PERSON_BK", "projeto orquidea")
        assert complete and [r.id for r in matches] == [active.id]
        assert not core.memory.store.recall_owner_records("PERSON_BK", "projeto inexistente")[0]
        assert topic_terms("Projeto Orquídea") == {"orquidea"}
    finally:
        core.close()
