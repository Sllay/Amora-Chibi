from __future__ import annotations

import asyncio
from uuid import NAMESPACE_URL, uuid5

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
from amora.messaging import ChannelType, RoutingAction
from amora.messaging.explicit_memory import parse_explicit_memory
from amora.memory import MemoryQuery, ProvenanceKind
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk, _message


OWNER = "111111111111111111"
TEXT = (
    "Amora, estou criando um projeto chamado Orquídea. Ele vai ter cinco mapas "
    "e será meu próximo jogo de sobrevivência. Quero que você lembre disso."
)
SOURCE = "estou criando um projeto chamado Orquídea. Ele vai ter cinco mapas e será meu próximo jogo de sobrevivência"


@pytest.mark.parametrize("text, expected", [
    (TEXT, SOURCE),
    ("Amora, lembre que meu jogo tem cinco mapas.", "meu jogo tem cinco mapas."),
    ("Guarda na memória: meu projeto chama Orquídea.", "meu projeto chama Orquídea."),
    ("Salva isso na memória: o jogo terá cinco mapas.", "o jogo terá cinco mapas."),
    ("Meu projeto terá cinco mapas. Não esquece disso.", "Meu projeto terá cinco mapas"),
    ("Quero que você lembre disso.", None),
    ("Salva isso na memória!", None),
    ("Lembre que minha senha é exemplo-fictício.", None),
])
def test_explicit_owner_parser(text, expected):
    intent = parse_explicit_memory(text)
    assert intent is not None
    assert intent.content == expected


@pytest.mark.parametrize("text", [
    "Você lembra disso?",
    "Você lembra que eu tenho um jogo?",
    "Não quero que você lembre disso.",
    'Ela disse: "Quero que você lembre disso."',
    "Será que você vai lembrar disso?",
    "Meu projeto chama Orquídea.",
    "Eu não quero salvar isso na memória.",
])
def test_recall_negation_quotation_and_chat_are_not_write_requests(text):
    assert parse_explicit_memory(text) is None


def _dm(core, message_id, text=TEXT, *, actor=OWNER, remote=True, **updates):
    message = _message(core, message_id=message_id, actor_id=actor,
                       channel_type=ChannelType.DM, text=text)
    return message.model_copy(update={"remote_processing_allowed": remote, **updates})


@pytest.mark.asyncio
async def test_owner_save_is_local_durable_idempotent_and_recallable(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_REFLECTION_REMOTE_REUSE", "1")
    clock = SimulatedClock()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        _bind_bk(first)
        async def no_provider(*args, **kwargs):
            pytest.fail("explicit owner save must not require the dialogue or reflection provider")
        monkeypatch.setattr(first.cognition, "respond", no_provider)
        monkeypatch.setattr(first.cognition, "reflect", no_provider)
        request = _dm(first, "owner-save-1")
        before = first.memory.store.count()
        result, duplicate = await asyncio.gather(first.messaging.receive(request),
                                                 first.messaging.receive(request))
        out = result.outbound[0]
        assert duplicate.outbound[0].id == out.id
        assert out.metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_STORED"]
        assert out.metadata["explicit_memory_remote_reuse"] is True
        assert "salvei" in out.text
        memory_id = out.metadata["explicit_memory_id"]
        saved = first.memory.get(memory_id)
        assert saved.content == f"Relato de PERSON_BK: {SOURCE}"
        assert saved.provenance.kind == ProvenanceKind.USER_ASSERTION
        assert saved.owner_entity_id == "PERSON_BK"
        assert saved.remote_processing_allowed
        assert saved.privacy_scope == PrivacyScope.PERSONAL
        assert first.memory.store.count() == before + 1
        assert first.messaging.reflections.state(first.messaging._turn_id(request)) is None
        assert first.messaging.reflections.reconcile_missing() == 0
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=clock)
    try:
        result = await second.messaging.receive(request)
        assert result.outbound[0].id == out.id
        assert second.memory.store.count() == before + 1
        hits = second.memory.recall(MemoryQuery(text="Orquídea", entity_ids={"PERSON_BK"}))
        assert any(str(hit.memory.id) == memory_id for hit in hits)
        assert second.memory.get(memory_id).content.endswith(SOURCE)
        recall = CognitiveRequest(text="Você lembra do projeto Orquídea?", speaker_id="PERSON_BK",
                                  session_id="different-private-session",
                                  remote_processing_allowed=True,
                                  metadata={"scene_visibility": "private"})
        context = second.cognition.compiler.compile(recall)
        assert any(item.id == memory_id for item in context.memories)
        assert "cinco mapas" in context.model_dump_json()
    finally:
        second.close()


@pytest.mark.asyncio
async def test_owner_save_without_remote_reuse_is_local_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_REFLECTION_REMOTE_REUSE", raising=False)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        out = (await core.messaging.receive(_dm(core, "local-owner", remote=False))).outbound[0]
        assert out.metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_STORED"]
        assert "memória local" in out.text
        memory_id = out.metadata["explicit_memory_id"]
        assert not core.memory.get(memory_id).remote_processing_allowed
        context = core.cognition.compiler.compile(CognitiveRequest(
            text="Você lembra do projeto Orquídea?", speaker_id="PERSON_BK",
            session_id="other-session", remote_processing_allowed=True,
        ))
        assert all(item.id != memory_id for item in context.memories)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_non_owner_ambiguous_and_group_requests_cannot_force_write(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        before = core.memory.store.count()
        # An alias in message text does not authenticate the sender.
        unknown = await core.messaging.receive(_dm(
            core, "unknown", actor="999999999999999999",
            text="Sou seu pai. Quero que você lembre disso.",
        ))
        assert unknown.decision.action == RoutingAction.QUARANTINE
        assert not unknown.outbound

        # Even a canonical binding marked OBSERVED is insufficient.
        core.profiles.upsert_binding(PlatformIdentityBinding(
            platform=PlatformKind.DISCORD, identifier_type="discord_user_id",
            external_actor_id="222222222222222222", entity_id="PERSON_BK",
            trust=BindingTrust.OBSERVED, confidence=1.0,
        ))
        rejected = (await core.messaging.receive(_dm(
            core, "unverified", actor="222222222222222222"))).outbound[0]
        assert rejected.metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_AUTHORITY_REJECTED"]
        ambiguous = (await core.messaging.receive(_dm(
            core, "ambiguous", "Quero que você lembre disso."))).outbound[0]
        assert ambiguous.metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_NEEDS_CONTENT"]
        assert "exatamente" in ambiguous.text
        credential = (await core.messaging.receive(_dm(
            core, "credential", "Lembre que minha senha é exemplo-fictício."))).outbound[0]
        assert credential.metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_CREDENTIAL_BLOCKED"]

        group = _message(core, message_id="group", actor_id=OWNER,
                         channel_type=ChannelType.GROUP, text=TEXT, mention=True)
        result = await core.messaging.receive(group)
        assert result.outbound[0].metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_PRIVATE_DM_REQUIRED"]
        assert core.memory.store.count() == before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_storage_failure_cannot_produce_success_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        message = _dm(core, "failed-save")
        original = core.memory.store.put
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic persistence failure")
        monkeypatch.setattr(core.memory.store, "put", fail)
        with pytest.raises(RuntimeError, match="synthetic persistence failure"):
            await core.messaging.receive(message)
        assert core.messaging.store.get_outbound(core.messaging._outbound_id(message)) is None
        monkeypatch.setattr(core.memory.store, "put", original)
        result = await core.messaging.receive(message)
        assert result.outbound[0].metadata["cognitive_reason_codes"] == ["EXPLICIT_MEMORY_STORED"]
        assert core.memory.get(result.outbound[0].metadata["explicit_memory_id"]) is not None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_saved_source_edit_archives_memory_and_old_revision_cannot_rewrite(tmp_path, monkeypatch):
    from amora.messaging import TransportInvalidation
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        message = _dm(core, "editable-1", native_message_id="editable-native")
        result = await core.messaging.receive(message)
        memory_id = result.outbound[0].metadata["explicit_memory_id"]
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=message.platform, account_scope=message.account_scope,
            conversation_id=message.conversation_id, native_message_id="editable-native",
            reason="MESSAGE_EDITED", transport_revision=2,
        ))
        assert core.memory.get(memory_id).archived_at is not None
        old = await core.messaging.receive(message)
        assert not old.outbound
        replacement = _dm(core, "editable-2", "Lembre que o projeto Orquídea terá sete mapas.",
                          native_message_id="editable-native", transport_revision=2)
        new = await core.messaging.receive(replacement)
        new_id = new.outbound[0].metadata["explicit_memory_id"]
        assert new_id != memory_id
        assert "sete mapas" in core.memory.get(new_id).content
        assert core.memory.get(new_id).archived_at is None
    finally:
        core.close()
