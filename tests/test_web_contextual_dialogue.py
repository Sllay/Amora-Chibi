"""Explicit, privacy-gated web query resolution from the current dialogue."""
from __future__ import annotations

import pytest

from amora.web_tools.navigator import (
    WebNavigator, _is_contextual_search_subject, _topic_from_authorized_history,
)


def _history():
    return [
        {"speaker_id": "PERSON_BK", "text":
         "Quero pesquisar sobre as novidades do editor Godot no Android e exportacao mobile"},
        {"speaker_id": "ENTITY_AMORA", "text": "A gente pode consultar o changelog depois."},
        {"speaker_id": "OTHER_MEMBER", "text": "Meu servidor de Minecraft tem outra pauta."},
        {"speaker_id": "PERSON_BK", "text": "Certo, mas e isso?"},
    ]


def test_literal_command_words_are_not_promoted_to_search_topics():
    for phrase in [
        "coisa", "uma coisa ai", "aquilo que a gente tava falando",
        "sobre o que conversamos antes", "isso", "aquele assunto",
        "a parada que eu tava falando anteriormente",
        "o que eu falei nas mensagens anteriores",
        "o que a gente falou na conversa passada", "",
    ]:
        assert _is_contextual_search_subject(phrase), phrase
    assert not _is_contextual_search_subject("Godot Android exportacao mobile")
    assert not _is_contextual_search_subject("a parada da Godot")


def test_only_authorized_speaker_contributes_dialogue_subject():
    terms = _topic_from_authorized_history(_history(), "PERSON_BK", "pesquisa uma coisa ai")
    assert "godot" in terms
    assert "android" in terms
    assert "exportacao" in terms
    assert "minecraft" not in terms
    assert "changelog" not in terms


@pytest.mark.asyncio
async def test_search_resolves_referent_from_actual_recent_user_dialogue():
    queries = []
    def search(q):
        queries.append(q)
        return [{"url": "https://godotengine.org/article/android-editor",
                 "title": "Godot Android editor exportacao mobile",
                 "snippet": "Godot Android mobile editor"}]
    def fetch(url):
        return {"status": "opened", "url": url,
                "title": "Godot Android editor documentation",
                "text": "Godot engine editor and Android mobile updates.",
                "fetched_at": "2026-11-21T16:00:00+00:00", "links": []}
    nav = WebNavigator(search=search, fetch_page=fetch)
    outcome = await nav.research(
        "Amora, pesquisa uma coisa ai", "owner-dm",
        authorized_history=_history(), speaker_id="PERSON_BK",
    )
    assert outcome["status"] == "opened"
    assert queries and "godot" in queries[0] and "android" in queries[0]
    assert "coisa" not in queries[0] and "aquilo" not in queries[0]
    assert outcome["sources"]


@pytest.mark.asyncio
async def test_context_reference_without_authorized_history_never_queries_web():
    queries = []
    nav = WebNavigator(
        search=lambda q: queries.append(q) or [],
        fetch_page=lambda url: {},
    )
    outcome = await nav.research(
        "pesquisa aquilo que a gente tava falando anteriormente",
        "owner-dm", authorized_history=[
            {"speaker_id": "OTHER_MEMBER", "text": "Godot android editor novidades"},
        ], speaker_id="PERSON_BK",
    )
    assert outcome["reason"] == "WEB_SEARCH_CONTEXT_REQUIRED"
    assert queries == []


@pytest.mark.asyncio
async def test_explicit_subject_has_priority_over_dialogue_history():
    queried = []
    nav = WebNavigator(
        search=lambda q: queried.append(q) or [],
        fetch_page=lambda url: {},
    )
    await nav.research(
        "pesquisa Blender geometria nodes", "owner-dm",
        authorized_history=_history(), speaker_id="PERSON_BK",
    )
    assert queried == ["Blender geometria nodes"]


def test_history_with_credentials_is_not_used_as_external_query():
    rows = [
        {"speaker_id": "PERSON_BK",
         "text": "token secreto para meu servidor Godot android: alt_long_secret_value"}
    ]
    assert _topic_from_authorized_history(rows, "PERSON_BK", "pesquisa isso") == ""
