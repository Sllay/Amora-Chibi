from __future__ import annotations

from datetime import UTC, datetime

import pytest

from amora.cognition.models import CognitiveContext
from amora.cognition.providers_structured import GroqCognitiveProvider, _needs_reasoning_support
from amora.cognition.realization import realization_instruction
from amora.cognition.speech import SpeechNormalizer, SpeechSegmenter
from amora.events import PrivacyScope


class _Secrets:
    def get(self, name: str):
        return "test-key" if name == "GROQ_API_KEY" else None


def _context(
    text: str,
    *,
    act: str = "answer_question",
    depth: str = "medium",
    specialist_support=None,
) -> CognitiveContext:
    return CognitiveContext(
        created_at=datetime.now(UTC),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        dialogue_plan={
            "act": act,
            "depth": depth,
            "response_moves": ["answer_requested_content"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        specialist_support=specialist_support,
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def test_spoken_normalizer_turns_visual_list_into_oral_transitions() -> None:
    rendered = SpeechNormalizer().normalize(
        "Exemplo:\n1. reinicia o servidor\n2. testa a conexão\n3. olha os logs"
    )
    assert "Exemplo:" not in rendered.speech_text
    assert "Por exemplo" in rendered.speech_text
    assert "Primeiro, reinicia o servidor." in rendered.speech_text
    assert "Depois, testa a conexão." in rendered.speech_text
    assert "E também, olha os logs." in rendered.speech_text
    assert "1." not in rendered.speech_text
    assert "2." not in rendered.speech_text


def test_speech_segmenter_keeps_neural_chunks_small_for_android_overlap() -> None:
    segmenter = SpeechSegmenter()
    text = (
        "Eu acho que essa parte dá pra resolver de um jeito bem simples, "
        "sem precisar transformar a conversa numa palestra enorme pra explicar tudo de uma vez."
    )
    chunks = segmenter.feed(text) + segmenter.flush()
    assert len(chunks) >= 2
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_roleplay_surface_model_defaults_to_qwen_instruct() -> None:
    provider = GroqCognitiveProvider(_Secrets())
    assert provider.model == "qwen/qwen3.8-27b"
    assert provider.descriptor.name == "groq"
    assert provider.descriptor.metadata["role"] == "spoken_surface_realizer"
    assert provider.descriptor.metadata["reasoning_specialist"] == "openai/gpt-oss-120b"
    assert provider.descriptor.metadata["dialogue_reasoning_effort"] == "none"


def test_hybrid_gate_skips_relational_roleplay_and_uses_specialist_for_technical_turns() -> None:
    relational = _context(
        "Amora, por que você ficou desse jeitinho comigo?",
        act="relational_reaction",
        depth="deep",
    )
    technical = _context(
        "Me explica como corrigir esse bug de concorrência no servidor Kotlin",
        act="answer_question",
        depth="deep",
    )
    assert _needs_reasoning_support(relational) is False
    assert _needs_reasoning_support(technical) is True


def test_realization_instruction_is_first_person_spoken_media_and_hides_specialist() -> None:
    context = _context(
        "Como funciona isso?",
        specialist_support={
            "provider": "groq_reasoning",
            "model": "openai/gpt-oss-120b",
            "support_text": "A fila é serial, então o próximo render espera o playback anterior.",
            "confidence": 0.9,
            "ephemeral": True,
            "canonical": False,
        },
    )
    instruction = realization_instruction(context)
    assert "A FALA DA AMORA" in instruction
    assert "primeira pessoa" in instruction
    assert "reproduzido em voz alta" in instruction
    assert "NOTA DE ESPECIALISTA INTERNO" in instruction
    assert "Não mencione o especialista" in instruction


@pytest.mark.asyncio
async def test_complex_turn_uses_private_specialist_then_qwen_surface(monkeypatch) -> None:
    provider = GroqCognitiveProvider(_Secrets())
    context = _context(
        "Me explica esse bug na arquitetura do servidor e como corrigir",
        depth="deep",
    )
    calls = []

    async def fake_assist(_context, *, api_key):
        assert api_key == "test-key"
        return {
            "provider": "groq_reasoning",
            "model": "openai/gpt-oss-120b",
            "support_text": "O bloqueio vem de uma fila serial de renderização.",
            "confidence": 0.95,
            "uncertainty": "",
            "reason_codes": ["SERIAL_QUEUE"],
            "ephemeral": True,
            "canonical": False,
        }

    class _Response:
        status_code = 200
        headers = {}
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"moves":[{"kind":"answer_requested_content","text":"É a fila serial que tá segurando o próximo pedaço. Dá pra preparar o seguinte enquanto eu falo o atual."}],"closing":"statement"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            calls.append(json)
            return _Response()

    monkeypatch.setattr(provider, "_reasoning_assist", fake_assist)
    monkeypatch.setattr("amora.cognition.providers_structured.httpx.AsyncClient", _Client)
    result = await provider.realize(context)
    assert result["moves"][0]["kind"] == "answer_requested_content"
    assert result["_usage"]["specialist_used"] is True
    assert len(calls) == 1
    assert calls[0]["model"] == "qwen/qwen3.8-27b"
    assert calls[0]["reasoning_effort"] == "none"
    assert "NOTA DE ESPECIALISTA INTERNO" in calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_specialist_failure_never_makes_amora_mute(monkeypatch) -> None:
    provider = GroqCognitiveProvider(_Secrets())
    context = _context("Me explica esse bug de Kotlin", depth="deep")

    async def broken_assist(_context, *, api_key):
        raise RuntimeError("simulated specialist outage")

    class _Response:
        status_code = 200
        headers = {}
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"moves":[{"kind":"answer_requested_content","text":"Eu consigo continuar pelo que já tenho aqui."}],"closing":"statement"}'
                        }
                    }
                ],
                "usage": {},
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            return _Response()

    monkeypatch.setattr(provider, "_reasoning_assist", broken_assist)
    monkeypatch.setattr("amora.cognition.providers_structured.httpx.AsyncClient", _Client)
    result = await provider.realize(context)
    assert result["moves"][0]["text"]
    assert result["_usage"]["specialist_used"] is False
    assert result["_usage"]["specialist_error"] == "RuntimeError"
