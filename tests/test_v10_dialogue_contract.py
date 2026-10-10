from __future__ import annotations

from amora.cognition.providers_v1 import GeminiCognitiveProvider, GroqCognitiveProvider


def test_v10_dialogue_contract_is_provider_independent_and_compact() -> None:
    groq = GroqCognitiveProvider._system_prompt()
    gemini = GeminiCognitiveProvider._system_prompt()

    assert groq == gemini
    assert "Amora é o próprio nome" in groq
    assert "vocativo/chamada" in groq
    assert "como posso ajudar hoje?" in groq
    assert "Não repita automaticamente" in groq
    assert "relação familiar ou afetiva" in groq
    assert len(groq) < 4_000


def test_v10_dialogue_contract_keeps_model_as_language_capacity() -> None:
    prompt = GroqCognitiveProvider._system_prompt()

    assert "Você não é a identidade dela" in prompt
    assert "não é a fonte canônica da verdade" in prompt
    assert "Não invente memórias, relações, fatos ou sentimentos" in prompt
