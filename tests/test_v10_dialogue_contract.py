from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from amora.cognition.models import CognitiveContext
from amora.cognition.providers_v1 import (
    GeminiCognitiveProvider,
    GroqCognitiveProvider,
    _dialogue_turns,
)
from amora.secrets import SecretStore


def _context() -> CognitiveContext:
    current_turn = uuid4()
    human_turn = uuid4()
    amora_turn = uuid4()
    return CognitiveContext(
        turn_id=current_turn,
        created_at=datetime.now(timezone.utc),
        speaker_id="SESSION_ANDROID_SPEAKER_1",
        target_id="ENTITY_AMORA",
        user_text="E tu, como tá depois de tudo que a gente fez hoje?",
        self_model={"core_identity": {"canonical_name": "Amora"}},
        relationships=[
            {
                "source": "ENTITY_AMORA",
                "relation_type": "family_child_parent",
                "target": "SESSION_ANDROID_SPEAKER_1",
                "confidence": 1.0,
            }
        ],
        recent_messages=[
            {
                "turn_id": str(human_turn),
                "speaker_id": "SESSION_ANDROID_SPEAKER_1",
                "addressee_id": "ENTITY_AMORA",
                "text": "Amora, tô meio cansado hoje.",
                "source": "stt",
                "status": "received",
            },
            {
                "turn_id": str(amora_turn),
                "speaker_id": "ENTITY_AMORA",
                "addressee_id": "SESSION_ANDROID_SPEAKER_1",
                "text": "Tô bem, só meio cansada,",
                "source": "cognitive",
                "status": "spoken",
            },
            {
                "turn_id": str(amora_turn),
                "speaker_id": "ENTITY_AMORA",
                "addressee_id": "SESSION_ANDROID_SPEAKER_1",
                "text": "mas curti o que a gente fez hoje.",
                "source": "cognitive",
                "status": "spoken",
            },
            # Defensive fixture: an older snapshot may contain the current input
            # because input persistence happens before context compilation.
            {
                "turn_id": str(current_turn),
                "speaker_id": "SESSION_ANDROID_SPEAKER_1",
                "addressee_id": "ENTITY_AMORA",
                "text": "E tu, como tá depois de tudo que a gente fez hoje?",
                "source": "stt",
                "status": "received",
            },
        ],
        dialogue_plan={
            "act": "answer_question",
            "objective": "answer directly and stay in the relationship/topic",
            "depth": "medium",
            "response_moves": ["answer_question", "relate_to_context_if_relevant"],
            "ask_followup": False,
            "offer_help": False,
            "style_register": "intimate_colloquial",
            "tone_tags": ["warm"],
            "fact_status": "not_requested",
            "epistemic_policy": "canonical_state_only",
            "language_role": "surface_realization",
            "reason_codes": ["LOCAL_DIALOGUE_ORCHESTRATOR"],
        },
        remote_processing_allowed=True,
    )


def test_v10_dialogue_contract_is_provider_independent_and_compact() -> None:
    groq = GroqCognitiveProvider._system_prompt()
    gemini = GeminiCognitiveProvider._system_prompt()

    assert groq == gemini
    assert "Core local já decidiu o ato social" in groq
    assert "realização linguística" in groq
    assert "ask_followup" in groq
    assert "offer_help" in groq
    assert "como posso ajudar hoje?" not in groq
    assert len(groq) < 3_500


def test_v10_dialogue_contract_keeps_model_as_language_capacity() -> None:
    prompt = GroqCognitiveProvider._system_prompt()

    assert "Você não é a identidade dela" in prompt
    assert "não é a fonte canônica da verdade" in prompt
    assert "Não invente memórias, relações, fatos ou sentimentos" in prompt
    assert "surface" not in prompt.lower() or "realização linguística" in prompt


def test_v10_history_is_projected_as_real_turns_and_segments_are_recombined() -> None:
    context = _context()

    assert _dialogue_turns(context) == [
        ("user", "Amora, tô meio cansado hoje."),
        ("assistant", "Tô bem, só meio cansada, mas curti o que a gente fez hoje."),
    ]


def test_v10_groq_receives_current_human_turn_once_with_native_roles(tmp_path) -> None:
    context = _context()
    provider = GroqCognitiveProvider(SecretStore(tmp_path / "secrets"))

    messages = provider._messages(context)
    assert [item["role"] for item in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == context.user_text
    assert sum(context.user_text in item["content"] for item in messages) == 1
    assert "recent_messages" not in messages[0]["content"]
    assert "ESTADO CANÔNICO SELECIONADO" in messages[0]["content"]
    assert "PLANO SEMÂNTICO LOCAL — AUTORITATIVO" in messages[0]["content"]
    assert '"offer_help":false' in messages[0]["content"]


def test_v10_gemini_uses_same_dialogue_projection(tmp_path) -> None:
    context = _context()
    provider = GeminiCognitiveProvider(SecretStore(tmp_path / "secrets"))

    payload = provider._payload(context)
    contents = payload["contents"]
    assert [item["role"] for item in contents] == ["user", "model", "user"]
    assert contents[-1]["parts"][0]["text"] == context.user_text
    serialized = str(payload)
    assert serialized.count(context.user_text) == 1
    instruction = payload["systemInstruction"]["parts"][0]["text"]
    assert "recent_messages" not in instruction
    assert "PLANO SEMÂNTICO LOCAL — AUTORITATIVO" in instruction
    assert '"ask_followup":false' in instruction
