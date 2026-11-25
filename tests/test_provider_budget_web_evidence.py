"""Regression tests for keeping real current-turn web evidence under provider caps."""
from datetime import datetime, timezone
from uuid import uuid4

from amora.cognition.context_budget import estimate_payload, fit_provider_payload
from amora.cognition.models import CognitiveContext


def _context():
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Qual é a conclusão da pesquisa?",
        web_research={
            "sources": [{
                "title": "Fonte verificada",
                "url": "https://example.org/reference",
                "text": "EVIDENCE " + "a" * 8000,
            }],
        },
    )


def _payload(ctx):
    return {
        "user_text": ctx.user_text,
        "web_research": ctx.web_research,
    }


def test_trim_source_once_and_keep_evidence_when_short_text_fits():
    original = _context()
    before = original.model_copy(deep=True)
    shortened = original.model_copy(deep=True)
    shortened.web_research["sources"][0]["text"] = (
        shortened.web_research["sources"][0]["text"][:600]
    )
    ceiling = estimate_payload(_payload(shortened)) + 1
    assert estimate_payload(_payload(original)) > ceiling

    projected, payload = fit_provider_payload(original, _payload, ceiling=ceiling)

    assert projected.web_research["sources"]
    assert projected.web_research["sources"][0]["text"] == shortened.web_research["sources"][0]["text"]
    assert projected.web_research["sources"][0]["url"] == "https://example.org/reference"
    assert estimate_payload(payload) <= ceiling
    assert original == before  # Original Core context is immutable to provider fitting.


def test_drop_source_only_if_even_its_shortened_version_does_not_fit():
    original = _context()
    without_source = original.model_copy(deep=True)
    without_source.web_research["sources"].clear()
    ceiling = estimate_payload(_payload(without_source)) + 1

    projected, payload = fit_provider_payload(original, _payload, ceiling=ceiling)

    assert projected.web_research["sources"] == []
    assert estimate_payload(payload) <= ceiling
    assert original.web_research["sources"][0]["text"].startswith("EVIDENCE ")


def test_deep_projection_preserves_diversity_before_omitting_sources():
    original = _context()
    original.web_research.update({"depth":"deep", "status":"opened"})
    original.web_research["sources"].append({
        "title":"Contraponto", "url":"https://second.example/claim", "text":"b"*8000})
    projected, payload = fit_provider_payload(original, _payload, ceiling=650, strict=True)
    assert len(projected.web_research["sources"]) == 2
    assert all(len(s["text"]) <= 600 for s in projected.web_research["sources"])
    meta = projected.web_research["provider_evidence_projection"]
    assert meta["original_read_count"] == 2
    assert meta["shortened_count"] == 2 and meta["omitted_count"] == 0
    assert len(original.web_research["sources"][0]["text"]) > 8000
