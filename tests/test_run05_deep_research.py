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


@pytest.mark.asyncio
async def test_resume_fresh_deep_pass_after_restart_only_reads_unfinished(notebook):
    from datetime import datetime, timezone
    searches, reads = [], []
    rows = [_result("first.example", "a"), _result("second.example", "b")]
    def fetch(url):
        reads.append(url)
        if "second.example" in url:
            raise WebFetchError("WEB_HTTP_503")
        return {**_page(url), "fetched_at":datetime.now(timezone.utc).isoformat()}
    nav = WebNavigator(fetch_page=fetch, search=lambda q: searches.append(q) or rows, workspace=notebook)
    first = await nav.research("pesquise a fundo Godot Android", "owner-dm")
    assert len(first["sources"]) == 1
    assert first["research_progress"]["partial"]
    assert len(searches) == 4
    stamp = first["sources"][0]["fetched_at"]
    reads.clear()
    def read_remaining(url):
        reads.append(url)
        return {**_page(url), "fetched_at":datetime.now(timezone.utc).isoformat()}
    restarted = WebNavigator(fetch_page=read_remaining,
        search=lambda q: (_ for _ in ()).throw(AssertionError("do not rediscover")),
        workspace=notebook)
    result = await restarted.research("continue pesquisando", "owner-dm")
    assert result["resumed_checkpoint"] is True
    assert result["research_progress"]["search_calls_started"] == 0
    assert result["research_progress"]["cached_excerpt_count"] == 1
    assert reads == ["https://second.example/b"]
    assert [r["url"] for r in result["references"]] == [r["url"] for r in first["references"]]
    cached = next(s for s in result["sources"] if s.get("cached_excerpt"))
    assert cached["fetched_at"] == stamp
    assert len(result["sources"]) == 2
    assert notebook.recent("other-dm") == []


def test_research_checkpoint_rejects_stale_future_mismatched_and_truncated_topics():
    from datetime import datetime, timedelta, timezone
    from amora.web_tools.deep_research import resumable_checkpoint
    now = datetime(2026,11,24,tzinfo=timezone.utc)
    record = {"action":"search","depth":"deep","resume_eligible":True,
              "topic":"Godot Android","references":[_result("first.example","a")],
              "discovered_at":now.isoformat()}
    assert resumable_checkpoint(record, "Godot Android", now=now)
    assert not resumable_checkpoint(record, "Godot Linux", now=now)
    assert not resumable_checkpoint({**record,"resume_eligible":False},"Godot Android",now=now)
    assert not resumable_checkpoint(record,"Godot Android",now=now+timedelta(seconds=901))
    assert not resumable_checkpoint(record,"Godot Android",now=now-timedelta(seconds=1))
