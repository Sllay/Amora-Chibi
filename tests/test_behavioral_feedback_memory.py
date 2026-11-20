"""Owner-sourced behavioral corrections survive reflection and shape later dialogue."""
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.prompt_frame import turn_frame
from amora.cognition.reflection_contract import MemoryExtraction, memory_schema
from amora.events import PrivacyScope
from amora.memory import MemoryKind, MemoryQuery
from test_reflection_v10 import StructuredProvider


class BehavioralProvider(StructuredProvider):
    async def propose(self, context):
        assert "COGNITIVE_REFLECTION" in context.reason_codes
        return {
            "memory_candidates": [{
                "kind": "semantic",
                "behavioral_signal": "behavioral_correction",
                "behavioral_topic": "question_repetition",
                "behavioral_scope": "general",
                "content": "BK corrigiu a Amora: não repetir a mesma pergunta se ele já respondeu.",
                "summary": "BK prefere que Amora evite perguntas repetidas já respondidas.",
                "confidence": 0.97,
                "importance": 0.96,
                "novelty": 0.92,
                "emotional_weight": 0.25,
                "relationship_relevance": 0.7,
                "future_consequence": 0.92,
                "goal_relevance": 0.8,
                "rarity": 0.2,
                "repetition": 0.0,
            }],
            "_usage": {"input_tokens": 20, "output_tokens": 45, "status_code": 200},
        }


def owner_request(*, scope="dm", remote=True, turn=None):
    kwargs = {} if turn is None else {"turn_id": turn}
    return CognitiveRequest(
        text="Amora, para de repetir a mesma pergunta quando eu já respondi!",
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id="discord:primary:feedback-dm",
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=remote,
        metadata={"surface": "platform_messaging", "platform": "discord",
                  "channel_type": scope, "memory_consolidation": True},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_explicit_owner_correction_is_stored_and_reused_in_future_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_REFLECTION_REMOTE_REUSE", raising=False)
    core = AmoraCore.bootstrap()
    try:
        core.cognition.register_provider(BehavioralProvider())
        request = owner_request()
        core.cognition.observe_input(request)
        assert core.messaging.reflections.enqueue(request)
        assert await core.messaging.reflections.process_once()
        stored = core.memory.recall(MemoryQuery(
            text="", kinds={MemoryKind.SEMANTIC},
            projection_recipient_id="PERSON_BK", projection_remote_only=True,
            required_tags={"amora_behavioral_feedback"},
        ))
        assert len(stored) == 1
        memory = stored[0].memory
        assert memory.remote_processing_allowed is True
        assert memory.metadata["behavioral_signal"] == "behavioral_correction"
        assert memory.owner_entity_id == "PERSON_BK"

        later = CognitiveRequest(
            text="E aí, como vai?", speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id=request.session_id, privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "platform": "discord", "channel_type": "dm"},
        )
        context = core.cognition.compiler.compile(later)
        assert "BEHAVIORAL_GUIDANCE_PROJECTED" in context.reason_codes
        assert any("perguntas repetidas" in item.content for item in context.behavioral_guidance)
        frame = turn_frame(context)
        assert frame["behavioral_guidance"]["may_override_core_policy"] is False

        no_remote = later.model_copy(update={"remote_processing_allowed": False})
        assert not core.cognition.compiler.compile(no_remote).behavioral_guidance
        group = later.model_copy(update={"metadata": {
            **later.metadata, "channel_type": "group",
        }})
        assert not core.cognition.compiler.compile(group).behavioral_guidance
        stranger = later.model_copy(update={"speaker_id": "PERSON_OUTRO"})
        assert not core.cognition.compiler.compile(stranger).behavioral_guidance
    finally:
        core.close()


@pytest.mark.asyncio
async def test_behavior_source_must_be_original_verified_owner_dm(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap()
    try:
        core.cognition.register_provider(BehavioralProvider())
        request = owner_request(scope="group")
        core.cognition.observe_input(request)
        assert core.messaging.reflections.enqueue(request)
        await core.messaging.reflections.process_once()
        records = core.memory.recall(MemoryQuery(
            text="", required_tags={"amora_behavioral_feedback"},
            projection_recipient_id="PERSON_BK", projection_remote_only=True,
        ))
        assert not records
    finally:
        core.close()


def test_typed_source_only_feedback_contract_is_conservative():
    schema = memory_schema()
    item = schema["properties"]["memory_candidates"]["items"]
    assert {"behavioral_signal", "behavioral_topic", "behavioral_scope"} <= set(item["required"])
    assert "behavioral_correction" in item["properties"]["behavioral_signal"]["enum"]
    assert "question_repetition" in item["properties"]["behavioral_topic"]["enum"]
    extracted = MemoryExtraction.model_validate({
        "memory_candidates": [{
            "kind": "semantic", "content": "BK prefere respostas curtas.",
            "summary": "Prefere respostas curtas.",
            **{name: 0.9 for name in (
                "confidence", "importance", "novelty", "emotional_weight",
                "relationship_relevance", "future_consequence",
                "goal_relevance", "rarity", "repetition"
            )},
        }]
    })
    assert extracted.memory_candidates[0].behavioral_signal == "none"
    assert extracted.memory_candidates[0].behavioral_topic == "none"
    assert extracted.memory_candidates[0].behavioral_scope == "contextual"
