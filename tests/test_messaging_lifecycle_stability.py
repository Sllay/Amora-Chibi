from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, ConversationMessage, MessageSource, MessageStatus
from amora.events import PrivacyScope
from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform, RoutingAction
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class CountingStructuredProvider:
    def __init__(self, *, name: str = "counting", delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self.descriptor = ProviderDescriptor(
            name=name,
            model=f"{name}-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            available=True,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=100,
        )

    async def realize(self, context):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        allowed = list(context.dialogue_plan["response_moves"])
        return {
            "moves": [{"kind": allowed[0], "text": "Resposta remota estável."}],
            "closing": "statement",
            "_usage": {"input_tokens": 12, "output_tokens": 4, "status_code": 200},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))


def _discord_direct_message(core: AmoraCore, *, message_id: str = "discord-msg-1") -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-channel-a",
        message_id=message_id,
        native_message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
                confidence=1.0,
            )
        ],
        text="Amora, me conta uma coisa engraçada sobre programação.",
        mentions_amora=True,
        timestamp=core.clock.now(),
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def test_recent_literal_history_is_scoped_to_the_current_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        web_marker = "SEGREDO_LITERAL_DA_SESSAO_WEB"
        discord_marker = "CONTEXTO_LITERAL_DO_CANAL_DISCORD"
        for session_id, text in (
            ("WEB_SESSION_A", web_marker),
            ("MSG_DISCORD_CHANNEL_A", discord_marker),
        ):
            core.cognition.store.put_message(
                ConversationMessage(
                    turn_id=uuid4(),
                    speaker_id="PERSON_BK",
                    addressee_id="ENTITY_AMORA",
                    source=MessageSource.TEXT,
                    raw_text=text,
                    normalized_text=text,
                    privacy_scope=PrivacyScope.PERSONAL,
                    remote_processing_allowed=True,
                    status=MessageStatus.RECEIVED,
                    session_id=session_id,
                    started_at=core.clock.now(),
                )
            )

        context = core.cognition.compiler.compile(
            CognitiveRequest(
                text="Continua esse assunto.",
                speaker_id="PERSON_BK",
                target_id="ENTITY_AMORA",
                session_id="MSG_DISCORD_CHANNEL_A",
                privacy_scope=PrivacyScope.PERSONAL,
                remote_processing_allowed=True,
            )
        )
        recent_text = "\n".join(str(item.get("text") or "") for item in context.recent_messages)
        assert discord_marker in recent_text
        assert web_marker not in recent_text
        assert "RECENT_HISTORY_SESSION_SCOPED" in context.reason_codes
    finally:
        core.close()


@pytest.mark.asyncio
async def test_duplicate_discord_delivery_reuses_one_cognitive_turn_and_one_outbound(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    provider = CountingStructuredProvider()
    core.cognition.register_provider(provider)
    message = _discord_direct_message(core)
    try:
        first = await core.messaging.receive(message)
        second = await core.messaging.receive(message)

        assert first.decision.action == RoutingAction.RESPOND
        assert len(first.outbound) == 1
        assert second.duplicate is True
        assert len(second.outbound) == 1
        assert first.outbound[0].id == second.outbound[0].id
        assert first.outbound[0].metadata["cognitive_turn_id"] == second.outbound[0].metadata["cognitive_turn_id"]
        assert provider.calls == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_structured_provider_attempt_budget_fails_over_to_local_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    provider = CountingStructuredProvider(name="slow-structured", delay=0.2)
    core.cognition.register_provider(provider)
    try:
        request = CognitiveRequest(
            text="Me explica por que um programa pode travar.",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            session_id="BUDGET_SESSION",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={
                "provider_total_budget_seconds": 0.05,
                "provider_attempt_budget_seconds": 0.02,
            },
        )
        plan = await core.cognition.respond(request, fallback_text="Uhum.")

        assert provider.calls == 1
        assert plan.fallback_used is True
        assert plan.provider is None
        assert "PROVIDER_ATTEMPT_TIMEOUT" in plan.reason_codes
        assert "LOCAL_FALLBACK" in plan.reason_codes
    finally:
        core.close()
