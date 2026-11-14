from __future__ import annotations

from datetime import timedelta

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind
from amora.messaging import ChannelType, MessagingPlatform, TransportInvalidation
from amora.messaging.memory_disclosure import parse_disclosure_intent
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk, _message
from test_persistent_owner_recall import _memory, _dm, OWNER, SOURCE, SECRET_MARKER


ASK = "Amora, pode falar aqui o que lembra do meu projeto Orquídea?"


@pytest.mark.parametrize("text, valid", [
    (ASK, True),
    ("Amora, pode compartilhar neste canal a memória sobre Orquídea?", True),
    ("Compartilhe aqui a lembrança sobre meu projeto Orquídea.", True),
    ("Autorizo você a compartilhar neste grupo o que lembra do projeto Orquídea.", True),
    ("Pode falar aqui tudo que lembra de mim?", False),
    ("Pode compartilhar aqui todas as minhas memórias?", False),
    ("Pode falar aqui o que lembra de mim?", False),
])
def test_disclosure_parser_requires_explicit_scope(text, valid):
    intent = parse_disclosure_intent(text)
    assert intent is not None and intent.valid is valid


@pytest.mark.parametrize("text", [
    "Amora, lembra do projeto Orquídea?",
    "Pode falar aqui sobre o projeto Orquídea?",
    "Pode contar aqui uma história?",
    "Não pode falar aqui o que lembra do projeto Orquídea.",
    '"Pode falar aqui o que lembra do projeto Orquídea?"',
    "Ela disse que pode falar aqui o que lembra do projeto Orquídea.",
])
def test_normal_dialogue_does_not_authorize_private_disclosure(text):
    assert parse_disclosure_intent(text) is None


def _group(core, mid, text=ASK, *, actor=OWNER, channel="group-a", **updates):
    return _message(core, message_id=mid, actor_id=actor,
                    channel_type=ChannelType.GROUP, text=text,
                    mention=True, conversation_id=channel).model_copy(
                        update={"remote_processing_allowed": True, **updates})


@pytest.mark.asyncio
async def test_verified_owner_one_turn_disclosure_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_REFLECTION_REMOTE_REUSE", raising=False)
    clock = SimulatedClock()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        _bind_bk(first)
        original = _dm(first, "share-save", SOURCE + " Quero que você lembre disso.")
        saved = (await first.messaging.receive(original)).outbound[0]
        mid = saved.metadata["explicit_memory_id"]
        _memory(first, SECRET_MARKER)
        assert not first.memory.get(mid).remote_processing_allowed
    finally:
        first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        async def no_provider(*args, **kwargs):
            pytest.fail("disclosure and owner recall must remain local")
        monkeypatch.setattr(second.cognition, "respond", no_provider)
        monkeypatch.setattr(second.cognition, "reflect", no_provider)
        group = _group(second, "share-once")
        first_out = (await second.messaging.receive(group)).outbound[0]
        assert first_out.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert "cinco mapas" in first_out.text and "sobrevivência" in first_out.text
        assert SECRET_MARKER not in first_out.text
        grant = first_out.metadata["memory_disclosure"]
        assert grant["record_id"] == mid
        assert grant["session_id"] == second.messaging._session_ref(group)
        assert grant["turn_id"] == str(second.messaging._turn_id(group))
        duplicate = (await second.messaging.receive(group)).outbound[0]
        assert duplicate.id == first_out.id
        memory = second.memory.get(mid)
        assert memory.privacy_scope == PrivacyScope.PERSONAL
        assert not memory.remote_processing_allowed
        assert second.messaging.reflections.state(second.messaging._turn_id(group)) is None
        # A question without new permission does not reuse the grant.
        for channel in ("group-a", "group-b"):
            out = (await second.messaging.receive(_group(second, "recall-"+channel,
                "Amora, lembra do projeto Orquídea?", channel=channel))).outbound[0]
            assert out.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_SHARED_SCOPE_RESTRICTED"]
            assert "cinco mapas" not in out.text
        ctx = second.cognition.compiler.compile(CognitiveRequest(
            text="O projeto Orquídea", speaker_id="PERSON_BK", remote_processing_allowed=True,
            session_id=second.messaging._session_ref(group),
            metadata={"surface": "platform_messaging", "channel_type": "group"}))
        assert all(item.id != mid for item in ctx.memories)
        assert SOURCE not in ctx.model_dump_json()
        assert not second.messaging.store.disclosure_is_current(
            first_out.model_copy(update={"conversation_id": "group-b"}))
    finally:
        second.close()


@pytest.mark.asyncio
async def test_denials_never_disclose_and_topic_excerpt_excludes_other_sentences(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        memory = _memory(core, SOURCE + " " + SECRET_MARKER + " é outro assunto.")
        _memory(core, "Projeto Segredo contém segredo protegido.", scope=PrivacyScope.PRIVATE)
        _memory(core, "Projeto Alheio pertence a outra pessoa.", owner="ENTITY_CHATGPT")
        third = "222222222222222222"
        core.profiles.upsert_binding(PlatformIdentityBinding(
            platform=PlatformKind.DISCORD, identifier_type="discord_user_id",
            external_actor_id=third, entity_id="PERSON_BK", trust=BindingTrust.OBSERVED))
        denied = (await core.messaging.receive(_group(core, "denied", actor=third))).outbound[0]
        assert denied.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORITY_REJECTED"]
        assert "cinco mapas" not in denied.text
        for mid, text in [
            ("all", "Pode falar aqui tudo que lembra de mim?"),
            ("private", "Pode falar aqui o que lembra do projeto Segredo?"),
            ("foreign", "Pode falar aqui o que lembra do projeto Alheio?"),
            ("missing", "Pode falar aqui o que lembra do projeto Inexistente?"),
        ]:
            out = (await core.messaging.receive(_group(core, mid, text))).outbound[0]
            assert not out.metadata.get("memory_disclosure")
            assert "cinco mapas" not in out.text and "segredo protegido" not in out.text
        out = (await core.messaging.receive(_group(core, "excerpt"))).outbound[0]
        assert out.metadata["memory_disclosure"]["record_id"] == str(memory.id)
        assert "Orquídea terá cinco mapas e será de sobrevivência" in out.text and SECRET_MARKER not in out.text
        _memory(core, "Projeto Orquídea terá um protótipo.")
        ambiguous = (await core.messaging.receive(_group(core, "ambiguous"))).outbound[0]
        assert ambiguous.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AMBIGUOUS"]
        assert "cinco mapas" not in ambiguous.text
        by_id = (await core.messaging.receive(_group(core, "reviewed-id",
            f"Compartilhe aqui a memória {memory.id}."))).outbound[0]
        assert by_id.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert SECRET_MARKER in by_id.text  # explicitly requested entire reviewed record
    finally:
        core.close()


@pytest.mark.asyncio
async def test_configured_policy_overrides_owner_and_source_archive_cancels_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        _bind_bk(core)
        memory = _memory(core, SOURCE)
        monkeypatch.setenv("AMORA_CONFIDENTIAL_LITERALS_JSON", '["cinco mapas"]')
        blocked = (await core.messaging.receive(_group(core, "blocked"))).outbound[0]
        assert blocked.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_POLICY_BLOCKED"]
        assert "cinco mapas" not in blocked.text
        assert not blocked.metadata.get("memory_disclosure")
        monkeypatch.delenv("AMORA_CONFIDENTIAL_LITERALS_JSON")
        request = _group(core, "before-archive")
        out = (await core.messaging.receive(request)).outbound[0]
        core.memory.archive(memory.id)
        assert not core.messaging.store.disclosure_is_current(out)
        replay = await core.messaging.receive(request)
        assert not replay.outbound
        assert core.messaging.store.get_outbound(out.id).status.value == "cancelled"
        memory2 = _memory(core, "Projeto Aurora gera biomas.")
        pending = (await core.messaging.receive(_group(core, "expiry",
            "Pode falar aqui o que lembra do projeto Aurora?"))).outbound[0]
        assert core.messaging.store.disclosure_is_current(pending)
        clock.advance(timedelta(minutes=6))
        claimed = core.messaging.store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert pending.id not in {item.id for item in claimed}
        assert core.messaging.store.get_outbound(pending.id).status.value == "cancelled"
        assert core.memory.get(memory2.id).archived_at is None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_owner_automatic_reflection_record_eligible_and_source_edit_blocks(tmp_path, monkeypatch):
    from test_reflection_v10 import StructuredProvider
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        class Provider(StructuredProvider):
            async def propose(self, context):
                bundle = await super().propose(context)
                bundle["memory_candidates"][0]["content"] = SOURCE
                bundle["memory_candidates"][0]["summary"] = SOURCE
                bundle["belief_hypotheses"] = []
                bundle["action_suggestions"] = []
                return bundle
        core.cognition.register_provider(Provider())
        msg = _dm(core, "reflection-original", SOURCE)
        msg = msg.model_copy(update={"native_message_id": "original-native"})
        assert (await core.messaging.receive(msg)).outbound
        assert await core.messaging.reflections.process_once()
        out = (await core.messaging.receive(_group(core, "reflect-share"))).outbound[0]
        assert out.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert "cinco mapas" in out.text
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=msg.platform, account_scope=msg.account_scope,
            conversation_id=msg.conversation_id, native_message_id=msg.native_source_message_id,
            reason="MESSAGE_EDITED", transport_revision=2))
        assert not core.messaging.store.disclosure_is_current(out)
        claimed = core.messaging.store.claim_outbound(platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert out.id not in {item.id for item in claimed}
    finally:
        core.close()


@pytest.mark.asyncio
async def test_remote_hrana_disclosure_uses_persistent_source_and_atomic_outbound(tmp_path, monkeypatch):
    from test_remote_context_batching import _remote_core
    core, server = _remote_core(tmp_path, monkeypatch)
    try:
        _bind_bk(core)
        core.memory.store.fts_enabled = False
        memory = _memory(core, SOURCE, remote=False)
        request = _group(core, "remote-share")
        out = (await core.messaging.receive(request)).outbound[0]
        assert out.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert out.metadata["memory_disclosure"]["record_id"] == str(memory.id)
        assert "cinco mapas" in out.text
        assert core.messaging.store.disclosure_is_current(out)
        assert core.messaging.store.get_outbound(out.id).text == out.text
        core.memory.archive(memory.id)
        claimed = core.messaging.store.claim_outbound(
            platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert out.id not in {item.id for item in claimed}
        assert core.messaging.store.get_outbound(out.id).status.value == "cancelled"
    finally:
        core.close()
