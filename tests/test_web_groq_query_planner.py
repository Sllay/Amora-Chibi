"""Groq/Qwen web-query planner: grounded, bounded, no extra Core LLM turns."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from amora.web_tools.query_planner import (
    DEFAULT_MODEL, GROQ_URL, GroqQueryPlanner, validated_queries,
)
from amora.web_tools.navigator import WebNavigator, WebFetchError


@pytest.mark.parametrize("raw,expected", [
    (
        {"queries": [
            "Godot Engine Android latest release changes",
            "site:godotengine.org Godot Android editor update"
        ]},
        [
            "Godot Engine Android latest release changes",
            "site:godotengine.org Godot Android editor update",
        ],
    ),
    ({"queries": ["Forrest Frank OKAY gospel", "OK Wikipedia grammar"]}, []),
    ({"queries": ["https://localhost/internal", "Godot Android changelog"]},
     ["Godot Android changelog"]),
    ({"queries": ["Godot Android news", "Godot Android news"]},
     ["Godot Android news"]),
    ({"queries": []}, []),
    ({"queries": "Godot Android"}, []),
])
def test_qwen_model_queries_are_validated_against_original_topic(raw, expected):
    assert validated_queries(raw, "novidade android godot engine") == expected


@pytest.mark.asyncio
async def test_groq_qwen_query_is_short_lived_and_uses_existing_api_key(monkeypatch):
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return httpx.Response(200, json={
                "choices": [{
                    "message": {"content": json.dumps({
                        "queries": [
                            "Godot Engine Android latest release changes",
                            "site:godotengine.org Godot Android editor update",
                        ],
                    })}
                }]
            })
    monkeypatch.setattr("amora.web_tools.query_planner.httpx.AsyncClient", FakeClient)
    planner = GroqQueryPlanner("existing-groq-key")
    queries = await planner.plan(
        "Okay entao ou amora pesquisa novidade sobre android na web da godot engine",
        "novidade android godot engine",
    )
    assert len(queries) == 2
    assert calls[1][1] == GROQ_URL
    payload = calls[1][2]["json"]
    assert payload["model"] == DEFAULT_MODEL
    assert payload["reasoning_effort"] == "none"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_completion_tokens"] <= 230
    assert calls[1][2]["headers"]["Authorization"] == "Bearer existing-groq-key"
    assert "request" in payload["messages"][1]["content"]
    assert "Okay" in payload["messages"][1]["content"]
    assert "memories" not in payload["messages"][1]["content"]
    assert calls[0][1]["trust_env"] is False


@pytest.mark.asyncio
async def test_llm_unavailable_does_not_break_deterministic_search(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            return httpx.Response(429, json={"error": "rate limited"})
    monkeypatch.setattr("amora.web_tools.query_planner.httpx.AsyncClient", FakeClient)
    planner = GroqQueryPlanner("key")
    result = await planner.plan("Amora pesquisa novidades Android Godot",
                                 "novidades Android Godot")
    assert result == ["novidades Android Godot"]


@pytest.mark.asyncio
async def test_bad_model_output_is_never_used_as_search_query(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"choices": [{
                "message": {"content": '{"queries":["gospel okay lyrics","Wikipedia OK"]}'}
            }]})
    monkeypatch.setattr("amora.web_tools.query_planner.httpx.AsyncClient", FakeClient)
    assert await GroqQueryPlanner("key").plan(
        "Okay Amora pesquisa novidade sobre Android Godot",
        "novidade Android Godot",
    ) == ["novidade Android Godot"]


@pytest.mark.asyncio
async def test_no_key_or_feature_disabled_shortcircuits_without_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("provider called for disabled planner")
    monkeypatch.setattr("amora.web_tools.query_planner.httpx.AsyncClient", forbidden)
    no_key = GroqQueryPlanner(None)
    assert not no_key.available
    assert await no_key.plan("pesquise Godot", "Godot") == ["Godot"]
    monkeypatch.setenv("AMORA_WEB_QUERY_PLANNER_ENABLED", "0")
    planner = GroqQueryPlanner("key")
    assert not planner.available
    assert await planner.plan("pesquise Godot", "Godot") == ["Godot"]


@pytest.mark.asyncio
async def test_planned_search_tries_second_semantic_query_if_first_irrelevant():
    class Planner:
        def __init__(self):
            self.messages = []
        async def plan(self, raw, fallback):
            self.messages.append((raw, fallback))
            return [
                "Godot Engine Android latest release changes",
                "Godot Android editor release notes",
            ]

    calls = []
    def query_provider(query):
        calls.append(("search", query))
        if "latest release" in query:
            return [{
                "url": "https://en.wikipedia.org/wiki/OK",
                "title": "OK okay grammar",
                "snippet": "A common English word",
            }]
        return [{
            "url": "https://godotengine.org/article/android-editor-news",
            "title": "Godot Engine Android editor releases",
            "snippet": "Latest Android builds",
        }]

    def fetch(url):
        calls.append(("fetch", url))
        return {"status": "opened", "url": url,
                "title": "Godot Editor for Android",
                "text": "Godot Android release documentation",
                "fetched_at": "2026-11-20T16:00:00+00:00",
                "links": []}

    planner = Planner()
    navigator = WebNavigator(fetch_page=fetch, search=query_provider, planner=planner)
    result = await navigator.research(
        "Okay entao ou amora pesquisa novidade sobre android na web da godot engine",
        "owner-dm",
    )
    assert result["status"] == "opened"
    assert len(planner.messages) == 1
    assert planner.messages[0][1] == "novidade android godot engine"
    assert calls[0] == ("search", "Godot Engine Android latest release changes")
    assert calls[1] == ("search", "Godot Android editor release notes")
    assert calls[2][0] == "fetch"
    assert all("wikipedia" not in ref["url"] for ref in result["references"])
    assert result["query"] == "Godot Android editor release notes"


@pytest.mark.asyncio
async def test_model_planner_never_runs_for_direct_open_and_link_follow():
    class ForbiddenPlanner:
        async def plan(self, *args):
            raise AssertionError("Planner should be search-only")
    def fetch(url):
        return {
            "status": "opened", "url": url,
            "title": "Godot Android",
            "text": "Godot Android release notes article read",
            "fetched_at": "2026-11-20T16:00:00+00:00",
            "links": [{"url": "https://godotengine.org/article/a", "title": "Another"}],
        }
    navigator = WebNavigator(fetch_page=fetch, planner=ForbiddenPlanner())
    page = await navigator.research(
        "Amora, acesse https://godotengine.org/releases", "owner-dm"
    )
    assert page["status"] == "opened"
    followed = await navigator.research("abre o link 1", "owner-dm")
    assert followed["status"] == "opened"


@pytest.mark.asyncio
async def test_planner_timeout_preserves_original_query():
    class SlowPlanner:
        async def plan(self, *args):
            raise asyncio.TimeoutError
    searched = []
    def search(query):
        searched.append(query)
        return [{
            "url": "https://godotengine.org/blog/android",
            "title": "Godot Android updates",
            "snippet": "Latest Godot Engine on Android",
        }]
    def fetch(url):
        return {
            "status": "opened", "url": url,
            "title": "Godot Android docs", "text": "Godot Android release documentation and mobile editor changes.",
            "links": [], "fetched_at": "2026-11-20T16:00:00+00:00",
        }
    navigator = WebNavigator(fetch_page=fetch, search=search, planner=SlowPlanner())
    result = await navigator.research(
        "Okay, Amora pesquisa novidades sobre Android na web da Godot Engine",
        "owner-dm",
    )
    assert result["status"] == "opened"
    assert searched == ["novidades Android Godot Engine"]
