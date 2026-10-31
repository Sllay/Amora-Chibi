from datetime import datetime, timezone

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.providers_v1 import _dialogue_plan_text
from amora.events import PrivacyScope
from amora.language import PerspectiveFrame, PerspectiveStack
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc))


def test_conservative_utterance_acir_canonicalizes_participants(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        acir = core.acir.compile_utterance(
            "eu falei contigo ontem",
            PerspectiveStack(
                PerspectiveFrame(speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA")
            ),
        )

        assert acir.speaker == "PERSON_BK"
        assert acir.addressee == "ENTITY_AMORA"
        assert acir.intent == "communicate"
        assert acir.proposition.type == "utterance"
        assert acir.proposition.roles["speaker"] == "PERSON_BK"
        assert acir.proposition.roles["addressee"] == "ENTITY_AMORA"
        assert acir.proposition.roles["content"] == "eu falei contigo ontem"
        assert acir.unresolved_references == []
    finally:
        core.close()


def test_normal_cognitive_context_compiles_acir_before_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        context = core.cognition.compiler.compile(
            CognitiveRequest(
                text="Amora, me explica isso.",
                speaker_id="PERSON_BK",
                target_id="ENTITY_AMORA",
                privacy_scope=PrivacyScope.PERSONAL,
                remote_processing_allowed=True,
            )
        )

        assert context.acir is not None
        assert context.acir["speaker"] == "PERSON_BK"
        assert context.acir["addressee"] == "ENTITY_AMORA"
        assert context.acir["proposition"]["roles"]["speaker"] == "PERSON_BK"
        assert context.acir["proposition"]["roles"]["addressee"] == "ENTITY_AMORA"
        assert context.acir["proposition"]["roles"]["content"] == "Amora, me explica isso."
        assert context.dialogue_plan["input_acir"] == context.acir
        assert "ACIR_COMPILED" in context.reason_codes
        assert "ACIR_BOUND_TO_DIALOGUE_PLAN" in context.reason_codes

        provider_projection = _dialogue_plan_text(context)
        assert '"input_acir"' in provider_projection
        assert '"speaker":"PERSON_BK"' in provider_projection
        assert '"addressee":"ENTITY_AMORA"' in provider_projection
    finally:
        core.close()


def test_auth_context_never_builds_or_exports_acir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        context = core.cognition.compiler.compile(
            CognitiveRequest(
                text="segredo de autenticação",
                speaker_id="PERSON_BK",
                target_id="ENTITY_AMORA",
                privacy_scope=PrivacyScope.AUTH,
                remote_processing_allowed=True,
            )
        )

        assert context.acir is None
        assert "input_acir" not in context.dialogue_plan
        assert "ACIR_REDACTED_PRIVATE_SCOPE" in context.reason_codes
        assert context.remote_processing_allowed is False
    finally:
        core.close()


def test_reflection_replan_keeps_the_same_input_acir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        context = core.cognition.compiler.compile(
            CognitiveRequest(
                text="Eu comecei um jogo novo.",
                speaker_id="PERSON_BK",
                target_id="ENTITY_AMORA",
                privacy_scope=PrivacyScope.PERSONAL,
                metadata={"cognitive_mode": "reflection"},
            )
        )

        assert context.acir is not None
        assert context.dialogue_plan["input_acir"] == context.acir
        assert "COGNITIVE_REFLECTION" in context.reason_codes
    finally:
        core.close()
