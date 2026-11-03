from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from amora.cognition.models import CognitiveContext
from amora.cognition.provider_circuit import CircuitState, ProviderCircuitRegistry
from amora.cognition.provider_resilience import (
    ResilientProviderProxy,
    StructuredRealizationProviderError,
    classify_provider_failure,
    retry_after_seconds,
)
from amora.cognition.providers import ProviderError
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass


def _context() -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Tu achou isso estranho também?",
        self_model={"core_identity": {"canonical_name": "Amora"}},
        relationships=[],
        mood={"valence": 0.0, "arousal": 0.2, "dominance": 0.0},
        drives={},
        dialogue_plan={
            "act": "answer_question",
            "objective": "answer directly",
            "depth": "short",
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def _descriptor(name: str = "fake") -> ProviderDescriptor:
    return ProviderDescriptor(
        name=name,
        model=f"{name}-model",
        capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
        quality=0.5,
        latency_score=0.5,
        cost_score=0.0,
        provider_class=ProviderClass.REMOTE_EPHEMERAL,
        allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
        supports_structured_output=True,
    )


def test_retry_after_parses_seconds_and_http_date() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("2.5", now=now) == 2.5
    assert retry_after_seconds("Mon, 02 Nov 2026 12:00:07 GMT", now=now) == 7.0
    assert retry_after_seconds("garbage", now=now) is None


def test_failure_classification_keeps_deterministic_4xx_non_retryable() -> None:
    auth = classify_provider_failure(ProviderError("nope", status_code=401))
    rate = classify_provider_failure(ProviderError("slow", status_code=429, retry_after="3"))
    upstream = classify_provider_failure(ProviderError("oops", status_code=503))

    assert auth.retryable is False
    assert auth.code == "PROVIDER_HTTP_401"
    assert rate.retryable is True
    assert rate.retry_after_seconds == 3.0
    assert upstream.retryable is True
    assert upstream.code == "PROVIDER_UPSTREAM_5XX"


class SequencedProvider:
    def __init__(self, sequence) -> None:
        self.descriptor = _descriptor()
        self.sequence = list(sequence)
        self.calls = 0
        self.context_ids: list[int] = []

    async def realize(self, context):
        self.calls += 1
        self.context_ids.append(id(context))
        value = self.sequence.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.mark.asyncio
async def test_short_retry_after_is_respected_with_bounded_inline_retry() -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    provider = SequencedProvider([
        ProviderError("busy", status_code=429, retry_after="0.4"),
        {"moves": [{"kind": "answer_question", "text": "Sim, achei estranho."}], "closing": "statement"},
    ])
    context = _context()
    proxy = ResilientProviderProxy(
        provider,
        base_delay_seconds=0.1,
        jitter_fraction=0.0,
        max_inline_retry_delay_seconds=1.0,
        sleep_fn=fake_sleep,
    )

    result = await proxy.realize(context)

    assert provider.calls == 2
    assert provider.context_ids == [id(context), id(context)]
    assert sleeps == [0.4]
    assert result["moves"][0]["text"] == "Sim, achei estranho."


@pytest.mark.asyncio
async def test_long_retry_after_is_not_slept_inside_dialogue_turn() -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    error = ProviderError("busy", status_code=429, retry_after="60")
    provider = SequencedProvider([error])
    proxy = ResilientProviderProxy(
        provider,
        max_inline_retry_delay_seconds=1.0,
        sleep_fn=fake_sleep,
    )

    with pytest.raises(ProviderError) as caught:
        await proxy.realize(_context())

    assert caught.value is error
    assert provider.calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_non_retryable_auth_failure_never_retries() -> None:
    provider = SequencedProvider([ProviderError("bad key", status_code=401)])
    proxy = ResilientProviderProxy(provider, max_retries=3)

    with pytest.raises(ProviderError):
        await proxy.realize(_context())

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_invalid_realization_gets_one_same_context_repair_attempt() -> None:
    provider = SequencedProvider([
        {"moves": [{"kind": "offer_help", "text": "Quer mais alguma coisa?"}], "closing": "statement"},
        {"moves": [{"kind": "answer_question", "text": "Também achei estranho."}], "closing": "statement"},
    ])
    context = _context()
    proxy = ResilientProviderProxy(
        provider,
        base_delay_seconds=0.0,
        max_validation_retries=1,
    )

    result = await proxy.realize(context)

    assert provider.calls == 2
    assert provider.context_ids == [id(context), id(context)]
    assert result["moves"][0]["kind"] == "answer_question"


@pytest.mark.asyncio
async def test_exhausted_structured_repair_becomes_non_retryable_provider_failure() -> None:
    invalid = {"moves": [{"kind": "offer_help", "text": "Quer mais alguma coisa?"}], "closing": "statement"}
    provider = SequencedProvider([invalid, invalid])
    context = _context()
    proxy = ResilientProviderProxy(
        provider,
        base_delay_seconds=0.0,
        max_validation_retries=1,
        max_retries=3,
    )

    with pytest.raises(StructuredRealizationProviderError) as caught:
        await proxy.realize(context)

    failure = classify_provider_failure(caught.value)
    assert provider.calls == 2
    assert provider.context_ids == [id(context), id(context)]
    assert caught.value.realization_error_code == "UNAUTHORIZED_REALIZATION_MOVE"
    assert failure.code == "PROVIDER_STRUCTURED_OUTPUT_INVALID"
    assert failure.retryable is False


def test_circuit_open_half_open_probe_and_close_lifecycle() -> None:
    registry = ProviderCircuitRegistry(half_open_probe_lease_seconds=5.0)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    task = TaskClass.SOCIAL_DIALOGUE.value

    record = registry.open(
        "groq",
        task,
        until=now + timedelta(seconds=10),
        failure_count=2,
        failure_code="HTTP_503",
    )
    assert record.state is CircuitState.OPEN
    assert registry.routing_decision(task, now=now).excluded_names == frozenset({"groq"})

    first_probe = registry.routing_decision(task, now=now + timedelta(seconds=11))
    assert first_probe.excluded_names == frozenset()
    assert first_probe.half_open_probe_names == frozenset({"groq"})
    assert registry.record("groq", task).state is CircuitState.HALF_OPEN

    concurrent = registry.routing_decision(task, now=now + timedelta(seconds=12))
    assert concurrent.excluded_names == frozenset({"groq"})
    assert concurrent.half_open_probe_names == frozenset()

    registry.close("groq", task)
    assert registry.record("groq", task).state is CircuitState.CLOSED
    assert registry.routing_decision(task, now=now + timedelta(seconds=13)).excluded_names == frozenset()


def test_failed_half_open_probe_reopens_until_new_deadline() -> None:
    registry = ProviderCircuitRegistry(half_open_probe_lease_seconds=2.0)
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    task = TaskClass.SOCIAL_DIALOGUE.value
    registry.open("groq", task, until=now, failure_count=1, failure_code="HTTP_503")
    probe = registry.routing_decision(task, now=now)
    assert probe.half_open_probe_names == frozenset({"groq"})

    reopened = registry.open(
        "groq",
        task,
        until=now + timedelta(seconds=30),
        failure_count=2,
        failure_code="HTTP_503",
    )
    assert reopened.state is CircuitState.OPEN
    assert registry.routing_decision(task, now=now + timedelta(seconds=1)).excluded_names == frozenset({"groq"})
