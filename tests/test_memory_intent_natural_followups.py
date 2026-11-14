from __future__ import annotations

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.memory.recall_intent import parse_recall_intent, topic_terms
from amora.messaging.memory_disclosure import parse_disclosure_intent
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk
from test_persistent_owner_recall import _memory, _dm, SOURCE, SECRET_MARKER
from test_owner_memory_disclosure import _group

REQUEST = "Amora, pode falar aqui o que lembra do meu projeto Orquídea?"
EXACT_PRODUCTION = "Pior que n tou inventando:\n\n" + REQUEST


@pytest.mark.parametrize("text", [
    EXACT_PRODUCTION,
    "Não tô inventando: Amora, pode falar aqui o que lembra do projeto Orquídea?",
    "Mas eu não estou inventando: Amora, pode falar aqui o que lembra do projeto Orquídea?",
    "Então, Amora, pode falar aqui oque lembra do projeto Orquídea?",
    "Olha, Amora, pode falar aqui o que lembra do projeto Orquídea?",
])
def test_explicit_authorization_after_bounded_lead_in(text):
    intent = parse_disclosure_intent(text)
    assert intent is not None and intent.valid
    assert topic_terms(intent.topic) == {"orquidea"}


@pytest.mark.parametrize("text", [
    "Não autorizei isso:\n" + REQUEST,
    "Se eu disser:\n" + REQUEST,
    "Ela disse:\n" + REQUEST,
    "Não tou inventando:\n> " + REQUEST,
    "Não tou inventando:\n“" + REQUEST + "”",
    "Não tou inventando:\n\x60" + REQUEST + "\x60",
    "Pior que n tou inventando:\nNão " + REQUEST,
    "Pior que n tou inventando: Amora, pode falar aqui sobre o projeto Orquídea?",
    "Pior que n tou inventando: Amora, lembra do projeto Orquídea?",
    "E a memória da Orquidia?",
])
def test_no_implicit_or_quoted_disclosure(text):
    assert parse_disclosure_intent(text) is None


@pytest.mark.parametrize("text, term", [
    ("E a memória da Orquidia?", "orquidia"),
    ("E a lembrança do projeto Orquídea?", "orquidea"),
    ("Posso te perguntar oque vc lembra sobre o meu projeto Orquídea? Que te falei no pv um tempinho atras", "orquidea"),
    ("Então, Amora, lembra do projeto Orquídea?", "orquidea"),
])
def test_natural_recall_questions(text, term):
    intent = parse_recall_intent(text)
    assert intent is not None and not intent.generic
    assert topic_terms(intent.topic) == {term}


@pytest.mark.asyncio
async def test_production_lobby_followup_does_not_confuse_privacy_with_missing_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        memory = _memory(core, SOURCE, remote=False)
        _memory(core, SECRET_MARKER)
        async def no_provider(*args, **kwargs):
            pytest.fail("recognized memory requests must use the persistent route")
        monkeypatch.setattr(core.cognition, "respond", no_provider)
        monkeypatch.setattr(core.cognition, "reflect", no_provider)
        original = (await core.messaging.receive(_group(core, "chat-original", REQUEST, channel="chat"))).outbound[0]
        assert original.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        short = (await core.messaging.receive(_group(core, "lobby-short", "E a memória da Orquidia?", channel="lobby"))).outbound[0]
        assert short.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_SHARED_SCOPE_RESTRICTED"]
        assert "cinco mapas" not in short.text
        assert "nenhum registro" not in short.text
        assert not short.metadata.get("memory_disclosure")
        full = (await core.messaging.receive(_group(core, "lobby-prefixed", EXACT_PRODUCTION, channel="lobby"))).outbound[0]
        assert full.metadata["cognitive_reason_codes"] == ["MEMORY_DISCLOSURE_AUTHORIZED"]
        assert full.metadata["memory_disclosure"]["record_id"] == str(memory.id)
        assert full.metadata["memory_disclosure"]["session_id"] != original.metadata["memory_disclosure"]["session_id"]
        assert "cinco mapas" in full.text and "sobrevivência" in full.text
        assert SECRET_MARKER not in full.text
        assert core.memory.get(memory.id).privacy_scope == PrivacyScope.PERSONAL
        assert not core.memory.get(memory.id).remote_processing_allowed
        private = (await core.messaging.receive(_dm(core, "private-followup",
                    "E a memória do projeto Orquídea?"))).outbound[0]
        assert private.metadata["cognitive_reason_codes"] == ["MEMORY_RECALL_PERSISTENT_FOUND"]
        assert private.metadata["memory_recall_ids"] == [str(memory.id)]
    finally:
        core.close()
