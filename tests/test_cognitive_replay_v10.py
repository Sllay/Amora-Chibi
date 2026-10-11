from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveContext, CognitiveRequest, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class ReplayProvider:
    def __init__(self, name: str, text: str, *, priority: int) -> None:
        self.text = text
        self.last_context: dict[str, object] | None = None
        self.descriptor = ProviderDescriptor(
            name=name,
            model=f"{name}-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=0.9,
            latency_score=0.9,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=priority,
        )

    async def realize(self, context):
        assert context.self_model
        self.last_context = context.model_dump(mode="json")
        allowed = list(context.dialogue_plan["response_moves"])
        return {
            "moves": [{"kind": allowed[0], "text": self.text}],
            "closing": "statement",
            "_usage": {"input_tokens": 10, "output_tokens": 5, "status_code": 200},
        }

    async def stream(self, context):
        # Replay is an immutable diagnostic sandbox inherited from the lower V1
        # layer. Production speech still uses realize()+local validation above.
        assert context.self_model
        yield ProviderDelta(text=self.text, input_tokens=10, output_tokens=5, status_code=200)
        yield ProviderDelta(done=True, finish_reason="stop", input_tokens=10, output_tokens=5, status_code=200)


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc))


def _state_counts(core: AmoraCore) -> dict[str, object]:
    report = core.health_report()
    return {
        "messages": core.cognition.store.message_count(),
        "journal": core.cognition.store.journal_count(),
        "memories": core.memory.store.count(),
        "world": core.world_store.claim_count(),
        "usage": tuple(
            (row["provider"], row["request_count"], row["success_count"], row["input_tokens"], row["output_tokens"])
            for row in core.cognition.store.usage_ledger()
        ),
        "identity": report["self_core_identity"],
    }


@pytest.mark.asyncio
async def test_replay_uses_same_snapshot_without_mutating_canonical_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        a = ReplayProvider("provider-a", "Resposta A.", priority=10)
        b = ReplayProvider("provider-b", "Resposta B.", priority=5)
        core.cognition.register_provider(a)
        core.cognition.register_provider(b)
        request = CognitiveRequest(
            text="Amora, o que você acha desse assunto?",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        plan = await core.cognition.respond(request)
        assert plan.provider == "provider-a"
        assert a.last_context is not None

        snapshot_entry = next(
            item
            for item in core.cognition.store.journal_for_turn(request.turn_id)
            if "REPLAY_SNAPSHOT" in item.reason_codes
        )
        snapshot = CognitiveContext.model_validate(snapshot_entry.payload["context"]).model_dump(mode="json")
        assert snapshot == a.last_context

        before = _state_counts(core)
        results = await core.cognition.replay(request.turn_id, provider_names=["provider-a", "provider-b"])
        after = _state_counts(core)

        assert before == after
        assert [item.provider for item in results] == ["provider-a", "provider-b"]
        assert [item.text for item in results] == ["Resposta A.", "Resposta B."]
        assert all(item.success for item in results)
        assert all("NO_STATE_MUTATION" in item.reason_codes for item in results)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_sensitive_turns_never_create_replay_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        request = CognitiveRequest(
            text="segredo",
            speaker_id="PERSON_BK",
            privacy_scope=PrivacyScope.SECRET,
            remote_processing_allowed=False,
        )
        await core.cognition.respond(request, fallback_text="Tudo bem.")
        journal = core.cognition.store.journal_for_turn(request.turn_id)
        assert not any("REPLAY_SNAPSHOT" in item.reason_codes for item in journal)
        with pytest.raises(KeyError):
            await core.cognition.replay(request.turn_id)
    finally:
        core.close()
