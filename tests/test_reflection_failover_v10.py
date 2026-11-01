from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, ProviderError
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class ReflectionProvider:
    def __init__(self, name: str, *, priority: int, fail: bool) -> None:
        self.fail = fail
        self.calls = 0
        self.descriptor = ProviderDescriptor(
            name=name,
            model=f"{name}-model",
            capabilities=frozenset({TaskClass.REASONING}),
            quality=0.9,
            latency_score=0.9,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=priority,
        )

    async def propose(self, context):
        self.calls += 1
        if self.fail:
            raise ProviderError("reflection failed", status_code=503)
        return {"memory_candidates": [], "belief_hypotheses": [], "action_suggestions": [], "tone": {"warmth": 0.5}, "_usage": {"input_tokens": 40, "output_tokens": 12, "status_code": 200}}


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_structured_reflection_fails_over_without_changing_concurrent_router_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        primary = ReflectionProvider("reflection-primary", priority=9, fail=True)
        secondary = ReflectionProvider("reflection-secondary", priority=4, fail=False)
        core.cognition.register_provider(primary); core.cognition.register_provider(secondary)
        request = CognitiveRequest(text="Guarda só o que realmente fizer sentido.", speaker_id="PERSON_BK", privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True)
        bundle = await core.cognition.reflect(request)
        assert primary.calls == 2
        assert secondary.calls == 1
        assert bundle.provider == "reflection-secondary"
        assert "REFLECTION_PROVIDER_FAILOVER_USED" in bundle.reason_codes
        assert core.cognition.router.descriptor("reflection-primary") is not None
        assert core.cognition.router.descriptor("reflection-secondary") is not None
        state = core.cognition.state()
        assert state["cognitive_provider_failures"]["reflection-primary"] == 1
    finally: core.close()
