from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, ProviderError
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class ScriptedProvider:
    def __init__(self, name: str, *, priority: int, mode: str) -> None:
        self.mode = mode; self.calls = 0
        self.descriptor = ProviderDescriptor(name=name, model=f"{name}-model", capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}), quality=0.9, latency_score=0.9, cost_score=0.0, provider_class=ProviderClass.REMOTE_EPHEMERAL, allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}), supports_streaming=False, supports_structured_output=True, context_window_tokens=100_000, priority=priority)

    async def realize(self, context):
        self.calls += 1
        if self.mode == "fail503": raise ProviderError("temporary", status_code=503)
        if self.mode == "rate_limit": raise ProviderError("quota", status_code=429, retry_after="60")
        if self.mode == "incomplete_fail": raise ProviderError("failed before validated realization", status_code=503)
        allowed = list(context.dialogue_plan["response_moves"])
        return {"moves": [{"kind": allowed[0], "text": f"Resposta de {self.descriptor.name}."}], "closing": "statement", "_usage": {"input_tokens": 20, "output_tokens": 8, "latency_ms": 3.0, "status_code": 200, "remaining_requests": 10}}


def _clock() -> SimulatedClock: return SimulatedClock(datetime(2026, 10, 10, 4, 0, tzinfo=timezone.utc))
def _request() -> CognitiveRequest: return CognitiveRequest(text="Amora, o que você acha desse jogo?", speaker_id="PERSON_BK", target_id="ENTITY_AMORA", privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True)


@pytest.mark.asyncio
async def test_provider_failure_before_validated_frame_fails_over(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        primary = ScriptedProvider("primary", priority=9, mode="fail503"); fallback = ScriptedProvider("secondary", priority=3, mode="success"); core.cognition.register_provider(primary); core.cognition.register_provider(fallback)
        plan = await core.cognition.respond(_request())
        assert primary.calls == 2; assert fallback.calls == 1; assert plan.provider == "secondary"; assert "PROVIDER_FAILOVER_USED" in plan.reason_codes
        assert core.cognition.state()["cognitive_provider_failures"]["primary"] == 1
        ledger = {row["provider"]: row for row in core.cognition.store.usage_ledger()}; assert ledger["primary"]["request_count"] == 1; assert ledger["secondary"]["success_count"] == 1
    finally: core.close()


@pytest.mark.asyncio
async def test_incomplete_realization_emits_nothing_and_safe_failover_is_allowed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        primary = ScriptedProvider("primary", priority=9, mode="incomplete_fail"); fallback = ScriptedProvider("secondary", priority=3, mode="success"); core.cognition.register_provider(primary); core.cognition.register_provider(fallback); request = _request(); plan = await core.cognition.respond(request)
        assert primary.calls == 2; assert fallback.calls == 1; assert plan.provider == "secondary"; assert plan.speech_segments == ["Resposta de secondary."]; assert "PROVIDER_FAILOVER_USED" in plan.reason_codes
        assistant_messages = [item for item in core.cognition.store.list_messages(turn_id=request.turn_id) if item.speaker_id == "ENTITY_AMORA"]
        assert assistant_messages; assert all(item.provider != "primary" for item in assistant_messages); assert any(item.provider == "secondary" for item in assistant_messages)
    finally: core.close()


@pytest.mark.asyncio
async def test_backoff_uses_canonical_clock_and_expires(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); core = AmoraCore.bootstrap(clock=clock)
    try:
        primary = ScriptedProvider("primary", priority=9, mode="fail503"); fallback = ScriptedProvider("secondary", priority=3, mode="success"); core.cognition.register_provider(primary); core.cognition.register_provider(fallback)
        await core.cognition.respond(_request()); assert primary.calls == 2
        await core.cognition.respond(_request()); assert primary.calls == 2
        clock.advance(timedelta(seconds=6)); await core.cognition.respond(_request()); assert primary.calls == 4
    finally: core.close()


@pytest.mark.asyncio
async def test_rate_limit_marks_quota_zero_until_cooldown_expires(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); core = AmoraCore.bootstrap(clock=clock)
    try:
        primary = ScriptedProvider("primary", priority=9, mode="rate_limit"); fallback = ScriptedProvider("secondary", priority=3, mode="success"); core.cognition.register_provider(primary); core.cognition.register_provider(fallback)
        await core.cognition.respond(_request()); assert core.cognition.router.descriptor("primary").quota_remaining == 0.0; assert primary.calls == 2
        await core.cognition.respond(_request()); assert primary.calls == 2
        clock.advance(timedelta(seconds=61)); await core.cognition.respond(_request()); assert primary.calls == 4
    finally: core.close()
