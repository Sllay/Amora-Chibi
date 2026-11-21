"""Durable web notebook contracts, no external network or credentials."""
from __future__ import annotations

import asyncio

import pytest

from amora.storage.database import AmoraDatabase
from amora.web_tools.navigator import WebNavigator, WebFetchError, parse_web_intent, WebIntent
from amora.web_tools.workspace_store import WebWorkspaceStore


def open_page(url):
    if "/denied" in url:
        raise WebFetchError("WEB_HTTP_403")
    return {
        "status": "opened", "url": url,
        "title": "Godot Engine" if "godotengine.org" in url else "Blender",
        "text": "The Godot Engine website contains official developer resources and examples.",
        "fetched_at": "2026-11-20T20:00:00Z", "links": [],
    }


@pytest.fixture
def notebook(tmp_path):
    db = AmoraDatabase(tmp_path / "amora.db")
    db.migrate()
    try:
        yield WebWorkspaceStore(db)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_restart_restores_exact_page_and_context_without_refetching(notebook):
    nav = WebNavigator(fetch_page=open_page, workspace=notebook)
    found = await nav.research("abre https://godotengine.org/", "owner-dm")
    assert found["status"] == "opened"
    visits = notebook.recent("owner-dm")
    assert visits[0]["entries"][0]["url"] == "https://godotengine.org/"
    assert visits[0]["entries"][0]["state"] == "read"
    assert visits[0]["entries"][0]["excerpt"].startswith("The Godot Engine")

    calls = []
    restarted = WebNavigator(fetch_page=open_page, workspace=notebook, search=lambda q:
        calls.append(q) or [{"url": "https://godotengine.org/releases",
                              "title": "Godot Engine release notes", "snippet": "Godot"}])
    searched = await restarted.research("pesquisa de novo e mim manda uma descrição", "owner-dm")
    assert searched["status"] == "opened"
    assert calls == ["Godot Engine"]
    assert "research_notebook" in searched
    assert searched["research_notebook"]["recent_pages"][0]["url"] == "https://godotengine.org/releases"


@pytest.mark.asyncio
async def test_bookmark_by_number_is_durable_and_uses_canonical_verified_url(notebook):
    finder = lambda _: [
        {"url": "https://godotengine.org/article/news", "title": "Godot news",
         "snippet": "Godot Engine"},
        {"url": "https://godotengine.org/article/release", "title": "Godot release",
         "snippet": "Godot Engine"},
    ]
    nav = WebNavigator(fetch_page=open_page, search=finder, workspace=notebook)
    result = await nav.research("pesquise Godot Engine", "owner-dm")
    assert result["status"] == "opened"
    saved = await nav.research("salva o link 2", "owner-dm")
    assert saved["status"] == "saved"
    assert notebook.bookmarks("owner-dm")[0]["url"] == "https://godotengine.org/article/release"
    assert notebook.bookmarks("another-dm") == []
    restarted = WebNavigator(fetch_page=open_page, workspace=notebook)
    listed = await restarted.research("mostra meus links salvos", "owner-dm")
    assert listed["references"][0]["url"] == "https://godotengine.org/article/release"
    assert listed["status"] == "results_only"
    assert (await restarted.research("salva o link 2", "another-dm"))["status"] == "failed"


@pytest.mark.asyncio
async def test_open_three_urls_retains_read_vs_unread_provenance(notebook):
    nav = WebNavigator(fetch_page=open_page, workspace=notebook)
    result = await nav.research(
        "abre https://godotengine.org/ https://godotengine.org/denied "
        "https://www.blender.org/", "owner-dm",
    )
    assert result["action"] == "open_many"
    assert len(result["sources"]) == 2
    assert len(result["references"]) == 3
    entries = notebook.recent("owner-dm")[0]["entries"]
    assert {e["url"] for e in entries} == {
        "https://godotengine.org/", "https://godotengine.org/denied",
        "https://www.blender.org/",
    }
    denied = next(e for e in entries if "/denied" in e["url"])
    assert denied["state"] == "found"
    assert not denied.get("excerpt")


@pytest.mark.asyncio
async def test_history_list_survives_worker_restart_and_includes_real_urls(notebook):
    nav = WebNavigator(fetch_page=open_page, workspace=notebook)
    await nav.research("abre https://godotengine.org/releases", "owner-dm")
    await nav.research("abre https://godotengine.org/learn", "owner-dm")
    new = WebNavigator(fetch_page=open_page, workspace=notebook)
    result = await new.research("mostra histórico de pesquisa", "owner-dm")
    assert result["status"] == "results_only"
    assert {r["url"] for r in result["references"]} == {
        "https://godotengine.org/releases", "https://godotengine.org/learn",
    }


@pytest.mark.asyncio
async def test_continuation_uses_evidence_not_raw_chat_words(notebook):
    queries = []
    nav = WebNavigator(
        fetch_page=open_page, workspace=notebook,
        search=lambda q: queries.append(q) or [{
            "url": "https://godotengine.org/article/news", "title": "Godot news",
            "snippet": "Godot Engine",
        }],
    )
    await nav.research("abre https://godotengine.org", "owner-dm")
    next_result = await nav.research("continue pesquisando", "owner-dm")
    assert next_result["status"] == "opened"
    assert queries == ["Godot Engine"]


@pytest.mark.asyncio
async def test_failed_reads_do_not_create_durable_false_evidence(notebook):
    nav = WebNavigator(fetch_page=open_page, workspace=notebook)
    bad = await nav.research("abre https://godotengine.org/denied", "owner-dm")
    assert bad["status"] == "failed"
    assert notebook.recent("owner-dm") == []


@pytest.mark.asyncio
async def test_screenshot_requests_do_not_persist_capture_authority(notebook):
    before = WebNavigator(fetch_page=open_page, workspace=notebook)
    await before.research("abre https://godotengine.org", "owner-dm")
    restarted = WebNavigator(fetch_page=open_page, workspace=notebook)
    retry = await restarted.research("envie a imagem", "owner-dm")
    assert retry["reason"] == "WEB_SCREENSHOT_NO_PENDING_REQUEST"


def test_parser_for_notebook_avoids_generic_conversation():
    assert parse_web_intent("mostra meus links salvos") == WebIntent("saved_links", "")
    assert parse_web_intent("mostra histórico de pesquisa") == WebIntent("web_history", "")
    assert parse_web_intent("salva o link 2") == WebIntent("pin", "2")
    assert parse_web_intent("salva esse link") == WebIntent("pin", "current")
    assert parse_web_intent("continue pesquisando") == WebIntent("search", "")
    assert parse_web_intent("salva esses links") == WebIntent("pin_results", "")
    assert parse_web_intent("descreve o link 2") == WebIntent("describe_link", "2")
    assert parse_web_intent("Eu salvei a memória dela ontem") is None



@pytest.mark.asyncio
async def test_saved_many_links_and_recovered_read_description_are_real_provenance(notebook):
    finder = lambda _: [
        {"url": "https://godotengine.org/article/a", "title": "Godot Engine A",
         "snippet": "Godot Engine"},
        {"url": "https://godotengine.org/article/b", "title": "Godot Engine B",
         "snippet": "Godot Engine"},
    ]
    nav = WebNavigator(fetch_page=open_page, search=finder, workspace=notebook)
    await nav.research("pesquisa Godot Engine", "owner-dm")
    remembered = await nav.research("descreve o link 2", "owner-dm")
    assert remembered["status"] == "results_only"
    assert remembered["references"][0]["url"] == "https://godotengine.org/article/b"
    assert remembered["stored_evidence"]["state"] == "read"
    assert remembered["stored_evidence"]["excerpt"].startswith("The Godot Engine")
    result = await nav.research("salva esses links", "owner-dm")
    assert result["saved_count"] == 2
    assert len(notebook.bookmarks("owner-dm")) == 2
    assert len(notebook.bookmarks("other-dm")) == 0
