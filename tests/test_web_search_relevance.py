"""Regression: Discord 'Okay ... pesquisa Godot Android' must not search for OK."""
from __future__ import annotations

import pytest

from amora.web_tools.navigator import (
    WebNavigator, _clean_search_query, _rank_search_results,
    parse_web_intent, _searx_search, _bing_rss_search, _ddg_search,
)

USER_DISCORD_MESSAGE = (
    "Okay entao ou amora pesquisa novidade sobre android na web da godot engine"
)
OFF_TOPIC = [
    {"url": "https://www.youtube.com/watch?v=HWduieNXgSg",
     "title": "Forrest Frank OKAY gospel lyrics",
     "snippet": "Lord I'm just grateful"},
    {"url": "https://en.wikipedia.org/wiki/OK",
     "title": "OK - Wikipedia",
     "snippet": "Okay is an English word"},
    {"url": "https://dictionary.cambridge.org/grammar/british-grammar/okay-ok",
     "title": "Okay, OK - Cambridge Dictionary",
     "snippet": "Grammar of okay"},
]
RELEVANT = [
    {"url": "https://godotengine.org/article/android-editor-updates",
     "title": "Godot Android editor improvements",
     "snippet": "New Godot Engine updates for Android"},
    {"url": "https://forum.godotengine.org/t/android-export-news",
     "title": "Android export changes in Godot",
     "snippet": "Godot Engine mobile development"},
]


@pytest.mark.parametrize("phrase,expected", [
    (USER_DISCORD_MESSAGE, "novidade android godot engine"),
    ("Amora, pesquisa novidades sobre Android na web da Godot Engine!",
     "novidades Android Godot Engine"),
    ("pesquisa Godot no Android", "Godot Android"),
    ("Amora, busque notícias da Godot Engine sobre Android",
     "notícias Godot Engine Android"),
    ("Pesquisa WebAssembly para Godot", "WebAssembly Godot"),
    ("pesquise C++ e GDScript no Godot", "C++ GDScript Godot"),
    ("Amora, pesquisa sobre Android e me mostra as fontes", "Android"),
])
def test_extracts_only_subject_not_opening_conversation(phrase, expected):
    result = parse_web_intent(phrase)
    assert result is not None and result.action == "search"
    assert result.argument.casefold() == expected.casefold()


def test_empty_explicit_search_does_not_search_just_the_command():
    intent = parse_web_intent("Amora, pesquisa")
    assert intent is not None and intent.action == "search"
    assert intent.argument == ""


@pytest.mark.parametrize("text,action", [
    ("Agora, Amora, como você está?", None),
    ("Só falei que pesquisamos semana passada", None),
    ("abre o link 2", "follow"),
    ("abre https://godotengine.org", "open"),
])
def test_direct_link_and_conversation_intent_unaffected(text, action):
    intent = parse_web_intent(text)
    assert (intent.action if intent else None) == action


def test_okay_music_grammar_links_never_qualify_as_godot_android_news():
    ranked = _rank_search_results(OFF_TOPIC + RELEVANT, "novidade android godot engine")
    assert [row["url"] for row in ranked] == [row["url"] for row in RELEVANT]
    assert all("okay" not in row["title"].casefold() for row in ranked)


def test_ranking_works_on_any_public_domain_without_topic_allowlist():
    rows = [
        {"url": "https://personal-dev-blog.example/blog/godot-android",
         "title": "My Godot Android news", "snippet": "Godot Engine"},
        {"url": "https://www.youtube.com/watch?v=HWduieNXgSg",
         "title": "Okay", "snippet": ""},
    ]
    ranked = _rank_search_results(rows, "android godot engine")
    assert len(ranked) == 1
    assert ranked[0]["url"].startswith("https://personal-dev-blog.example/")


@pytest.mark.asyncio
async def test_bad_first_provider_tries_next_engine_and_never_opens_unrelated(monkeypatch):
    monkeypatch.delenv("AMORA_WEB_SEARCH_ENDPOINT", raising=False)
    captured = []
    from amora.web_tools import navigator as mod

    def broken_bing(query):
        captured.append(("bing", query))
        return OFF_TOPIC

    def good_ddg(query):
        captured.append(("ddg", query))
        return RELEVANT

    def read_page(url):
        captured.append(("page", url))
        return {
            "status": "opened", "url": url,
            "title": "Godot Android documentation",
            "text": "The Godot editor on Android has new releases and changes.",
            "fetched_at": "2026-11-20T12:00:00+00:00",
            "links": [],
        }

    monkeypatch.setattr(mod, "_bing_rss_search", broken_bing)
    monkeypatch.setattr(mod, "_ddg_search", good_ddg)
    navigator = WebNavigator(fetch_page=read_page)
    result = await navigator.research(USER_DISCORD_MESSAGE, "owner-discord-dm")
    assert [x[0] for x in captured[:2]] == ["bing", "ddg"]
    assert captured[0][1] == "novidade android godot engine"
    assert captured[1][1] == "novidade android godot engine"
    assert result["status"] == "opened"
    assert all("youtube" not in ref["url"] for ref in result["references"])
    assert all("okay" not in ref["title"].casefold() for ref in result["references"])
    assert all(x[1].startswith(("https://godotengine.org",
                                "https://forum.godotengine.org"))
               for x in captured[2:])
    assert result["sources"]


@pytest.mark.asyncio
async def test_all_irrelevant_results_are_not_cited_or_opened():
    visited = []
    def read_page(url):
        visited.append(url)
        raise AssertionError("Should not request off-topic content")
    navigator = WebNavigator(fetch_page=read_page, search=lambda query: OFF_TOPIC)
    result = await navigator.research(USER_DISCORD_MESSAGE, "owner-discord-dm")
    assert result == {
        "action": "search", "status": "failed",
        "reason": "WEB_SEARCH_NO_RELEVANT_RESULTS",
        "sources": [], "references": [],
    }
    assert visited == []


@pytest.mark.asyncio
async def test_empty_explicit_query_fails_without_network():
    def forbidden(*args):
        raise AssertionError("No topic, no search request")
    navigator = WebNavigator(fetch_page=forbidden, search=forbidden)
    result = await navigator.research("Amora, pesquisa", "owner-discord-dm")
    assert result["status"] == "failed"
    assert result["reason"] == "WEB_SEARCH_QUERY_EMPTY"
