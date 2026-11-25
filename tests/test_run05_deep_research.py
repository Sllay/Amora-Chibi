"""Synthetic RUN05: bounded search, partial evidence and safe fallback."""
import asyncio
import threading

import pytest

from amora.web_tools.navigator import WebNavigator, WebFetchError, parse_web_intent
from amora.web_tools.deep_research import deep_search
from test_web_workspace_durable import notebook


def _result(host, slug):
    return {"url": f"https://{host}/{slug}", "title": "Godot Android release", "snippet": "Godot Android documentation"}


def _page(url):
    return {"status":"opened", "url":url, "title":"Godot Android",
            "text":"Godot Android editor documentation with release details. " * 8,
            "fetched_at":"2026-11-24T20:00:00Z", "links":[]}


def test_deep_mode_and_contextual_web_intent_do_not_change_quick():
    assert parse_web_intent("pesquise Godot Android").depth == "quick"
    intent = parse_web_intent("pesquise a fundo Godot Android")
    assert intent.depth == "deep" and intent.argument == "Godot Android"
    assert parse_web_intent("verifique na internet Godot Android").argument == "Godot Android"
    assert parse_web_intent("O que você acha de Godot Android?") is None


@pytest.mark.asyncio
async def test_deep_diverse_sources_fallback_and_persist(notebook):
    searches, reads, renders = [], [], []
    def search(query):
        searches.append(query)
        return [_result("docs.example", "a"), _result("docs.example", "b"),
                _result("forum.example", "c"), _result("blog.example", "d"),
                _result("other.example", "e")]
    def fetch(url):
        reads.append(url)
        if "forum.example" in url:
            raise WebFetchError("WEB_HTTP_403")
        return _page(url)
    class Reader:
        async def render(self, url):
            renders.append(url)
            return {**_page(url), "retrieval":"third_party_jina_reader"}
    nav = WebNavigator(fetch_page=fetch, search=search, reader=Reader(), workspace=notebook)
    result = await nav.research("pesquise a fundo Godot Android", "owner-dm")
    assert result["depth"] == "deep" and result["status"] == "opened"
    assert len(searches) == 4 and len(reads) == 5
    assert renders == ["https://forum.example/c"]
    assert len(result["sources"]) == 5
    assert all(item["state"] == "read" for item in result["references"])
    assert result["research_progress"]["cross_source_claims_verified"] is False
    assert len([e for e in notebook.recent("owner-dm")[0]["entries"] if e["state"]=="read"]) == 5
    assert notebook.recent("other-dm") == []


@pytest.mark.asyncio
async def test_deep_deadline_returns_completed_source_and_found_reference():
    nav = WebNavigator(fetch_page=_page, search=lambda _: [
        _result("first.example","a"), _result("slow.example","b")])
    async def read(url, **kwargs):
        if "slow.example" in url:
            await asyncio.sleep(10)
        return _page(url)
    nav._read_page = read
    result = await deep_search(nav,"Godot Android","pesquise a fundo Godot Android","owner",seconds=.4)
    assert result["status"] == "opened"
    assert len(result["sources"]) == 1
    assert len(result["references"]) == 2
    assert result["research_progress"]["partial"]
    assert result["research_progress"]["stop_reason"] == "deadline"


@pytest.mark.asyncio
async def test_denied_dns_target_never_falls_back_to_external_reader():
    def deny(url):
        raise WebFetchError("WEB_PRIVATE_NETWORK_DENIED")
    class Reader:
        async def render(self,url):
            raise AssertionError("must not bypass denied network")
    nav = WebNavigator(fetch_page=deny, reader=Reader())
    with pytest.raises(WebFetchError,match="WEB_PRIVATE_NETWORK_DENIED"):
        await nav._read_page("https://private-resolution.example/a")


@pytest.mark.asyncio
async def test_cancelled_thread_keeps_slot_until_actual_completion():
    release = threading.Event()
    started = threading.Event()
    def slow():
        started.set()
        release.wait(2)
    nav = WebNavigator(fetch_page=_page)
    task = asyncio.create_task(nav._call(slow))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert nav._semaphore._value == 1
    finally:
        release.set()
        for _ in range(100):
            if nav._semaphore._value == 2:
                break
            await asyncio.sleep(.01)
    assert nav._semaphore._value == 2
