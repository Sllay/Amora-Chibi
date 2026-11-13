from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from amora.app import AmoraCore
from amora.cognition.context import ProviderPrivacyGate
from amora.cognition.expression import expression_cue, expression_profile
from amora.cognition.models import CognitiveContext, CognitiveRequest, ConversationMessage, MessageSource, MessageStatus
from amora.cognition.prompt_frame import render_trusted_protocol
from amora.cognition.realization import realization_instruction
from amora.events import PrivacyScope
from amora.identity import Entity, EntityType, Relationship, build_initial_registry
from amora.time import SimulatedClock


def _privacy_item(
    scope: PrivacyScope,
    *,
    owner: str | None = None,
    actor: str | None = None,
    audience: tuple[str, ...] = (),
    remote: bool = True,
):
    return SimpleNamespace(
        privacy_scope=scope,
        owner_entity_id=owner,
        audience=set(audience),
        provenance=SimpleNamespace(actor_id=actor),
        remote_processing_allowed=remote,
    )


def test_memory_gate_uses_owner_and_audience_not_topic_words() -> None:
    private_alice = _privacy_item(
        PrivacyScope.PERSONAL, owner="PERSON_ALICE", audience=("PERSON_ALICE",)
    )
    assert ProviderPrivacyGate.memory_allowed(
        private_alice, remote=True, recipient_id="PERSON_ALICE"
    )
    assert not ProviderPrivacyGate.memory_allowed(
        private_alice, remote=True, recipient_id="PERSON_BK"
    )

    shared = _privacy_item(
        PrivacyScope.PERSONAL,
        owner="PERSON_ALICE",
        audience=("PERSON_BK",),
    )
    assert ProviderPrivacyGate.memory_allowed(shared, remote=True, recipient_id="PERSON_BK")
    assert not ProviderPrivacyGate.memory_allowed(
        _privacy_item(PrivacyScope.PERSONAL, actor=None),
        remote=True, recipient_id="PERSON_BK"
    )
    assert ProviderPrivacyGate.memory_allowed(
        _privacy_item(PrivacyScope.PERSONAL, actor="PERSON_BK"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert ProviderPrivacyGate.memory_allowed(
        _privacy_item(PrivacyScope.PUBLIC, owner="PERSON_ALICE"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert not ProviderPrivacyGate.memory_allowed(
        _privacy_item(PrivacyScope.SECRET, owner="PERSON_BK"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert not ProviderPrivacyGate.memory_allowed(
        _privacy_item(PrivacyScope.PERSONAL, owner="PERSON_BK", remote=False),
        remote=True, recipient_id="PERSON_BK"
    )


def test_world_claims_do_not_expose_personal_third_party_facts() -> None:
    def claim(scope, subject, source=None, remote=True):
        return SimpleNamespace(
            privacy_scope=scope, subject_id=subject, source_entity_id=source,
            remote_processing_allowed=remote,
        )

    assert not ProviderPrivacyGate.claim_allowed(
        claim(PrivacyScope.PERSONAL, "PERSON_ALICE"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert not ProviderPrivacyGate.claim_allowed(
        claim(PrivacyScope.PERSONAL, "ENTITY_AMORA", source="PERSON_ALICE"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert ProviderPrivacyGate.claim_allowed(
        claim(PrivacyScope.PERSONAL, "PERSON_BK"),
        remote=True, recipient_id="PERSON_BK"
    )
    assert ProviderPrivacyGate.claim_allowed(
        claim(PrivacyScope.PUBLIC, "PERSON_ALICE"),
        remote=True, recipient_id="PERSON_BK"
    )


def test_shared_session_recent_history_remains_recipient_scoped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(
        clock=SimulatedClock(datetime(2026, 11, 4, tzinfo=timezone.utc))
    )
    try:
        session = "test:shared-group"
        core.identities.add_entity(Entity(id="PERSON_ALICE", canonical_name="Alice",
                                         entity_type=EntityType.PERSON))
        core.identities.add_relationship(Relationship(
            source="PERSON_ALICE", relation_type="mother_of", target="ENTITY_AMORA"
        ))
        entries = (
            ("PERSON_ALICE", "ENTITY_AMORA", PrivacyScope.PERSONAL, "ALICE_PRIVATE_FIXTURE"),
            ("ENTITY_AMORA", "PERSON_ALICE", PrivacyScope.PERSONAL, "AMORA_TO_ALICE_PRIVATE_FIXTURE"),
            ("PERSON_BK", "ENTITY_AMORA", PrivacyScope.PERSONAL, "BK_OWN_CONTEXT"),
            ("ENTITY_AMORA", "PERSON_BK", PrivacyScope.PERSONAL, "AMORA_TO_BK_CONTEXT"),
            ("PERSON_ALICE", "ENTITY_AMORA", PrivacyScope.PUBLIC, "ALICE_PUBLIC_CONTEXT"),
        )
        for speaker, addressee, scope, text in entries:
            core.cognition.store.put_message(ConversationMessage(
                turn_id=uuid4(),
                speaker_id=speaker,
                addressee_id=addressee,
                source=MessageSource.TEXT,
                raw_text=text,
                normalized_text=text,
                spoken_text=text,
                privacy_scope=scope,
                remote_processing_allowed=True,
                started_at=core.clock.now(),
                status=MessageStatus.SPOKEN,
                session_id=session,
            ))

        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text="Amora, continua a conversa da gente.",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            session_id=session,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        ), token_budget=9000)
        rendered = " ".join(str(m.get("text")) for m in ctx.recent_messages)
        assert "BK_OWN_CONTEXT" in rendered
        assert "AMORA_TO_BK_CONTEXT" in rendered
        assert "ALICE_PUBLIC_CONTEXT" in rendered
        assert "ALICE_PRIVATE_FIXTURE" not in rendered
        assert "AMORA_TO_ALICE_PRIVATE_FIXTURE" not in rendered

        # Only the explicit public bootstrap family catalogue is an exception
        # to recipient ownership, not arbitrary edges involving Amora.
        public_family = {(r.source, r.relation_type, r.target)
                         for r in build_initial_registry().relationships
                         if r.relation_type in {"mother_of", "father_of", "child_of"}}
        assert all(
            "PERSON_BK" in {r["source"], r["target"]}
            or (r["source"], r["relation_type"], r["target"]) in public_family
            for r in ctx.relationships
        )
        assert not any(r["source"] == "PERSON_ALICE" for r in ctx.relationships)
        assert "relationships" not in ctx.self_model or not ctx.remote_processing_allowed
    finally:
        core.close()


def test_mature_language_and_context_are_provider_neutral_and_not_privacy_keywords() -> None:
    context = CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Qual fruta tu gosta? kkkkk",
        relationships=[{
            "source": "ENTITY_AMORA", "target": "PERSON_BK",
            "relation_type": "father",
        }],
        dialogue_plan={
            "act": "answer_question",
            "response_moves": ["answer_question"],
            "style_register": "intimate_colloquial",
            "tone_tags": ["playful"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        remote_processing_allowed=True,
    )
    profile = expression_profile(context)
    assert "double_entendre" in profile["conversation_freedom"]
    assert "adult_topics" in profile["conversation_freedom"]
    assert "media_reactions" in profile["conversation_freedom"]
    assert expression_cue(context)["profanity"]["level"] == "playful"
    protocol = render_trusted_protocol(context)
    instructions = realization_instruction(context)
    assert "conversation_freedom" in protocol
    assert "não exija que o usuário xingue primeiro" in instructions
    assert "uma interpretação inocente pode ser a correta" in instructions
    assert "violência ou gore" in instructions
    assert "não revele segredos" in instructions.lower()
    assert context.dialogue_plan["response_moves"] == ["answer_question"]


def test_shared_realization_preserves_voice_without_changing_authority():
    context = CognitiveContext(
        turn_id=uuid4(), created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        user_text="O que a gente estava falando?",
        expression_profile="core-v1", scene_visibility="group",
        dialogue_plan={"act": "answer_question", "response_moves": ["answer_question"],
                       "ask_followup": False, "offer_help": False, "closing_mode": "statement"},
    )
    original = context.model_dump(mode="json")
    instruction = realization_instruction(context)
    assert "conversa compartilhada" in instruction
    assert "sem copiar frases prontas" in instruction
    assert "histórico autorizado deste turno" in instruction
    assert "Não altere o objetivo" in instruction
    assert "ask_followup=False" in instruction
    assert "offer_help=False" in instruction
    assert context.model_dump(mode="json") == original
    private = context.model_copy(update={"scene_visibility": "private"})
    assert "conversa compartilhada" not in realization_instruction(private)


def test_shared_voice_is_style_only_and_independent_of_private_state():
    context = CognitiveContext(
        turn_id=uuid4(), created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        user_text="Oi Amora", expression_profile="core-v1", scene_visibility="group",
        dialogue_plan={"act": "answer_question", "response_moves": ["answer_question"]},
    )
    profile = expression_profile(context)
    voice = profile["shared_voice"]
    assert set(voice) == {"register", "social_range", "initiative", "grounded_banter",
                          "honest_uncertainty", "range_and_rhythm", "authority"}
    assert "sarcástica" in voice["social_range"]
    assert "palavrões naturais" in voice["grounded_banter"]
    assert "Não invente lembranças" in voice["honest_uncertainty"]
    changed = context.model_copy(update={"speaker_id": "PERSON_OTHER",
                                        "self_model": {"private": "SYNTHETIC_SECRET"},
                                        "user_text": "different topic"})
    assert expression_profile(changed)["shared_voice"] == voice
    private = context.model_copy(update={"scene_visibility": "private"})
    assert "shared_voice" not in expression_profile(private)
    assert "shared_voice" in render_trusted_protocol(context)
