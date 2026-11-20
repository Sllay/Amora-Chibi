"""Discord web links are Core-owned, even if model realizes fallback text."""
from __future__ import annotations

import asyncio

import pytest

from amora.messaging.web_delivery import (
    compose_web_delivery, requested_screenshot_source, screenshot_requested,
    neutralize_screenshot_claims,
)
from amora.web_tools.navigator import WebFetchError, WebIntent, WebNavigator, parse_web_intent


def test_search_links_survive_model_fallback_and_remain_clickable():
    message, capture = compose_web_delivery(
        "Hoje não consegui terminar a leitura das páginas.",
        intent=WebIntent("search", "godot android"),
        sources=[],
        references=[],
        session_references=[
            {"url": "https://godotengine.org/article/android-editor", "title": "Godot Android"},
            {"url": "https://forum.godotengine.org/t/mobile", "title": "Community"},
        ],
        owner_verified=True,
    )
    assert capture is None
    assert "não consegui terminar" in message
    assert "Resultados da busca (nem todos lidos):" in message
    assert "1. https://godotengine.org/article/android-editor" in message
    assert "2. https://forum.godotengine.org/t/mobile" in message


def test_model_cannot_invent_unverified_source_urls():
    content, capture = compose_web_delivery(
        "https://model-invented.example.org/fake",
        intent=WebIntent("search", "godot"),
        sources=[],
        references=[],
        owner_verified=True,
    )
    assert "Fontes consultadas:" not in content
    assert "Resultados da busca" not in content
    assert capture is None


def test_source_links_vs_unread_search_results_are_not_misrepresented():
    content, _ = compose_web_delivery(
        "Encontrei a atualização.",
        intent=WebIntent("search", "godot android"),
        sources=[{"url": "https://godotengine.org/article/android", "title": "Article"}],
        references=[
            "https://godotengine.org/article/android",
            "https://forum.godotengine.org/t/topic",
        ],
        owner_verified=True,
    )
    assert "Fontes consultadas:\nhttps://godotengine.org/article/android" in content
    assert "1. https://godotengine.org/article/android" in content
    assert "2. https://forum.godotengine.org/t/topic" in content


@pytest.mark.parametrize("text", [
    "Amora, tira um print do link 2",
    "Manda um print do site",
    "Amora, me envia screenshot da página",
    "Amora, pesquisa Godot Android e manda print da página",
])
def test_user_explicitly_requests_web_screenshot(text):
    assert screenshot_requested(text)


@pytest.mark.parametrize("text", [
    "Olha minhas imagens do Android",
    "A página tem desenhos bonitos",
    "Estou criando um editor de screenshots",
    "Amora, pesquisa a API screenshot do Godot",
])
def test_screenshot_not_triggered_by_generic_image_conversation(text):
    assert not screenshot_requested(text)


def test_print_of_numbered_link_is_an_explicit_web_follow():
    assert parse_web_intent("Amora, tira print do link 2") == WebIntent("follow", "2")


def test_screenshot_target_requires_verified_owner_and_explicit_request():
    intent = WebIntent("search", "godot android")
    source = {"url": "https://godotengine.org/article/android-editor"}
    kwargs = {"intent": intent, "sources": [source]}
    assert requested_screenshot_source(
        "me manda um print da página", owner_verified=True, **kwargs
    ) == "https://godotengine.org/article/android-editor"
    assert requested_screenshot_source(
        "pesquisa godot", owner_verified=True, **kwargs
    ) is None
    assert requested_screenshot_source(
        "manda print", owner_verified=False, **kwargs
    ) is None


def test_credential_urls_never_reach_external_screenshot_service():
    assert requested_screenshot_source(
        "Manda um print deste site",
        WebIntent("open", "https://example.org/data?access_token=private"),
        owner_verified=True,
    ) is None


def test_search_with_only_references_can_capture_first_result_as_a_screenshot():
    assert requested_screenshot_source(
        "Pesquisa novidades sobre Godot Android e manda print da página",
        WebIntent("search", "godot android"),
        references=[
            "https://godotengine.org/article/android-editor",
            "https://godotengine.org/releases",
        ],
        owner_verified=True,
    ) == "https://godotengine.org/article/android-editor"


@pytest.mark.asyncio
async def test_failed_new_search_invalidates_previous_result_references():
    ref = {"url": "https://godotengine.org/article/older", "title": "Godot Android older update", "snippet": "Godot Android"}
    def finder(query):
        if "Godot" in query:
            return [ref]
        return []
    navigator = WebNavigator(
        search=finder,
        fetch_page=lambda url: {
            "status": "opened", "url": url, "title": "Older",
            "text": "An article about Godot Android updates",
            "fetched_at": "2026-11-20T19:00:00+00:00", "links": [],
        },
    )
    assert (await navigator.research("pesquise Godot Android", "owner-dm"))["status"] == "opened"
    assert navigator.current_public_references("owner-dm") == [ref]
    outcome = await navigator.research("pesquise notícias de Blender Blender", "owner-dm")
    assert outcome["status"] == "failed"
    assert navigator.current_public_references("owner-dm") == []


@pytest.mark.asyncio
async def test_references_are_isolated_by_discord_dm_session():
    navigator = WebNavigator(search=lambda q: [{
        "url": "https://godotengine.org/article/android",
        "title": "Godot Android updates",
        "snippet": "Godot Android",
    }], fetch_page=lambda url: {
        "status": "opened", "url": url, "title": "Godot Android",
        "text": "Godot Android news update",
        "fetched_at": "2026-11-20T19:00:00+00:00", "links": [],
    })
    await navigator.research("pesquise Godot Android", "owner-dm")
    assert navigator.current_public_references("stranger-dm") == []


@pytest.mark.asyncio
async def test_current_turn_source_survives_provider_failure_without_storing_page_text():
    def read(url):
        return {
            "status": "opened", "url": url, "title": "Official article",
            "text": "A detailed page about Godot and Android functionality.",
            "fetched_at": "2026-11-20T18:00:00+00:00",
            "links": [{"url": "https://godotengine.org/releases", "title": "Releases"}],
        }
    navigator = WebNavigator(fetch_page=read)
    result = await navigator.research("abre https://godotengine.org/article/android", "owner-dm")
    assert result["status"] == "opened"
    delivery = navigator.current_delivery("owner-dm")
    assert delivery["sources"] == [{
        "url": "https://godotengine.org/article/android", "title": "Official article"
    }]
    assert "text" not in delivery["sources"][0]
    assert delivery["references"][0]["url"] == "https://godotengine.org/releases"
    outgoing, _ = compose_web_delivery(
        "Minha geração de texto falhou, mas encontrei a página.",
        intent=WebIntent("open", "https://godotengine.org/article/android"),
        sources=delivery["sources"], references=delivery["references"], owner_verified=True,
    )
    assert "Fontes consultadas:\nhttps://godotengine.org/article/android" in outgoing
    await navigator.research("pesquisa", "owner-dm")
    assert navigator.current_delivery("owner-dm")["sources"] == []


def test_giant_search_urls_do_not_overflow_outbound_limit():
    huge = "https://example.org/article?q=" + ("a" * 1800)
    outgoing, _ = compose_web_delivery(
        "Resposta verificada do modelo. " * 50,
        intent=WebIntent("search", "example"),
        sources=[{"url": huge}], references=[huge] * 5,
        owner_verified=True,
    )
    assert len(outgoing) < 12000



def test_screenshot_only_intent_is_not_ignored_or_confused_with_search():
    for sentence in (
        "Amora, tira um print dessa página e me envia",
        "Manda um print do site",
        "Amora, me envia screenshot da página",
    ):
        assert parse_web_intent(sentence) == WebIntent("capture", "current")
    assert parse_web_intent("pesquisa a API screenshot do Godot") == WebIntent(
        "search", "API screenshot Godot"
    )
    assert parse_web_intent(
        "pesquisa novidades Godot Android e manda print da página"
    ) == WebIntent("search", "novidades Godot Android")


@pytest.mark.asyncio
async def test_capture_current_page_uses_last_verified_dm_page_without_reopening():
    calls = []
    def read(url):
        calls.append(url)
        return {
            "status": "opened", "url": url, "title": "Godot",
            "text": "Known page contents for the user.",
            "fetched_at": "2026-11-20T19:00:00+00:00", "links": [],
        }
    nav = WebNavigator(fetch_page=read)
    opened = await nav.research("abre https://godotengine.org/releases", "owner-dm")
    assert opened["status"] == "opened"
    capture = await nav.research("tira print dessa página e me envia", "owner-dm")
    assert capture["status"] == "capture_pending"
    assert capture["sources"] == []
    assert calls == ["https://godotengine.org/releases"]
    prior = nav.current_delivery("owner-dm")
    assert requested_screenshot_source(
        "tira print dessa página e me envia",
        parse_web_intent("tira print dessa página e me envia"),
        sources=prior["sources"], references=prior["references"],
        owner_verified=True,
    ) == "https://godotengine.org/releases"
    assert (await nav.research("manda um print da página", "different-dm"))["reason"] == "WEB_SCREENSHOT_NO_PAGE"
    assert nav.current_delivery("different-dm")["references"] == []


@pytest.mark.asyncio
async def test_capture_only_without_previous_web_page_fails_explicitly():
    nav = WebNavigator(fetch_page=lambda _: (_ for _ in ()).throw(AssertionError()))
    result = await nav.research("manda print", "owner-dm")
    assert result == {"action": "capture", "status": "failed",
                      "reason": "WEB_SCREENSHOT_NO_PAGE",
                      "sources": [], "references": []}


def test_model_cannot_claim_print_delivery_on_behalf_of_discord():
    statement = "Encontrei dados da atualização. Tirei um print e enviei a imagem! O projeto usa Godot."
    cleaned = neutralize_screenshot_claims(statement)
    assert "Encontrei dados da atualização." in cleaned
    assert "O projeto usa Godot." in cleaned
    assert "enviei a imagem" not in cleaned
    assert "anexo no Discord" in cleaned
    unchanged = "Pesquisei como tirar print na Godot Engine."
    assert neutralize_screenshot_claims(unchanged) == unchanged


def test_screenshot_current_does_not_represent_old_page_as_new_source():
    text, _ = compose_web_delivery(
        "Captura solicitada.",
        intent=WebIntent("capture", "current"),
        sources=[{"url": "https://godotengine.org/releases"}],
        references=[{"url": "https://godotengine.org/article"}],
        owner_verified=True,
    )
    assert text == "Captura solicitada."



@pytest.mark.asyncio
async def test_numbered_search_results_survive_opening_a_leaf_page():
    entries = [
        {"url": f"https://example.org/godot-{n}",
         "title": f"Godot Android release {n}",
         "snippet": "Godot Android release article"}
        for n in range(1, 4)
    ]
    def read(url):
        return {"status": "opened", "url": url, "title": "Article",
                "text": "Godot Android official release content.",
                "fetched_at": "2026-11-20T19:00:00+00:00",
                "links": []}
    nav = WebNavigator(fetch_page=read, search=lambda q: entries)
    search = await nav.research("pesquise Godot Android", "owner-dm")
    assert search["status"] == "opened"
    followed2 = await nav.research("abre o link 2", "owner-dm")
    assert followed2["sources"][0]["url"] == entries[1]["url"]
    followed3 = await nav.research("abre o link 3", "owner-dm")
    assert followed3["sources"][0]["url"] == entries[2]["url"]


@pytest.mark.asyncio
async def test_browser_interstitial_is_not_counted_as_a_read_page():
    from amora.web_tools.navigator import WebFetchError
    nav = WebNavigator(fetch_page=lambda url: {
        "status": "opened", "url": url, "title": "Blocked",
        "text": "Just a moment... Checking your browser before proceeding.",
        "fetched_at": "2026-11-20T19:00:00+00:00", "links": [],
    })
    result = await nav.research("abre https://example.org/blocked", "owner-dm")
    assert result["status"] == "failed"
    assert result["reason"] == "WEB_PAGE_CONTENT_UNAVAILABLE"
    assert nav.current_delivery("owner-dm")["sources"] == []



def test_simple_send_image_followup_requires_previous_capture_lease():
    # Exact follow-up from the real Discord incident.
    assert parse_web_intent("Envie a imagem") == WebIntent(
        "capture_retry", "previous"
    )
    assert parse_web_intent("Amora, me manda a foto!") == WebIntent(
        "capture_retry", "previous"
    )
    # A reference to a new picture should not send a previous web screenshot.
    assert parse_web_intent("envia a imagem do coelho") is None
    assert not screenshot_requested("Envie a imagem")


@pytest.mark.asyncio
async def test_send_image_retries_only_recent_verified_screenshot_in_same_dm():
    nav = WebNavigator(fetch_page=lambda url: {
        "status": "opened", "url": url, "title": "Godot",
        "text": "Godot official website and resources.",
        "fetched_at": "2026-11-20T20:00:00+00:00",
        "links": [],
    })
    assert (await nav.research(
        "abre https://godotengine.org", "owner-dm"
    ))["status"] == "opened"
    assert (await nav.research(
        "Envie a imagem", "owner-dm"
    ))["reason"] == "WEB_SCREENSHOT_NO_PENDING_REQUEST"

    nav.remember_capture_request("owner-dm", "https://godotengine.org/")
    retry = await nav.research("Envie a imagem", "owner-dm")
    assert retry == {
        "action": "capture_retry", "status": "capture_pending",
        "sources": [], "references": [],
    }
    url = requested_screenshot_source(
        "Envie a imagem", WebIntent("capture_retry", "previous"),
        owner_verified=True,
        authorized_retry_url=nav.current_capture_target("owner-dm"),
    )
    assert url == "https://godotengine.org/"
    assert requested_screenshot_source(
        "Envie a imagem", WebIntent("capture_retry", "previous"),
        owner_verified=False, authorized_retry_url=url,
    ) is None
    assert requested_screenshot_source(
        "Envie a imagem", WebIntent("capture_retry", "previous"),
        owner_verified=True,
    ) is None
    # No cross-user copy of the link.
    assert nav.current_capture_target("another-dm") is None
    assert (await nav.research(
        "Envie a imagem", "another-dm"
    ))["status"] == "failed"

    # An explicit new navigation invalidates the previous retry lease.
    await nav.research("abre https://godotengine.org/releases", "owner-dm")
    assert nav.current_capture_target("owner-dm") is None


@pytest.mark.asyncio
async def test_web_screenshot_retry_lease_expires_without_persistent_memory():
    nav = WebNavigator(fetch_page=lambda url: (_ for _ in ()).throw(
        AssertionError("retry must not fetch another page")
    ))
    nav.remember_capture_request("owner", "https://godotengine.org/")
    current = nav.last_capture_requests["owner"]
    nav.last_capture_requests["owner"] = (current[0] - 601, current[1])
    assert nav.current_capture_target("owner") is None
    failed = await nav.research("Envie a imagem", "owner")
    assert failed["reason"] == "WEB_SCREENSHOT_NO_PENDING_REQUEST"


def test_web_screenshot_retry_cannot_forward_sensitive_url():
    from amora.web_tools.navigator import WebFetchError
    import pytest
    nav = WebNavigator(fetch_page=lambda url: {})
    # Only call inside the event loop in the async contract below.
    assert requested_screenshot_source(
        "Envie a imagem", WebIntent("capture_retry", "previous"),
        owner_verified=True,
        authorized_retry_url="https://example.org/?access_token=secret",
    ) is None



@pytest.mark.parametrize("text,expected", [
    ("pesquisa de novo e me manda uma descrição", ""),
    ("pesquisa de novo e mim manda uma descrição", ""),
    ("Amora, pesquisa novamente e me explica", ""),
    ("pesquise mais detalhes sobre isso", ""),
    ("pesquisa sobre isso de novo", ""),
    ("pesquisa de novo sobre Godot Android e me manda uma descrição",
     "Godot Android"),
    ("pesquise novidades de Godot e manda um resumo", "novidades Godot"),
    ("pesquisa sobre Blender e me manda uma descrição", "Blender"),
])
def test_search_command_excludes_delivery_instructions(text, expected):
    assert parse_web_intent(text) == WebIntent("search", expected)


@pytest.mark.asyncio
async def test_anaphoric_search_uses_recent_opened_godot_page_not_description_words(caplog):
    caplog.set_level("INFO", logger="amora.web")
    queries = []
    def search(query):
        queries.append(query)
        return [{"url": "https://godotengine.org/article/latest-news",
                 "title": "Godot Engine updates", "snippet": "Godot Engine news"}]
    def read(url):
        return {
            "status": "opened", "url": url,
            "title": "Priorities – Godot Engine" if url.endswith("/")
                     else "Godot Engine updates",
            "text": "The website has information about game development.",
            "fetched_at": "2026-11-20T19:00:00+00:00", "links": [],
        }
    nav = WebNavigator(fetch_page=read, search=search)
    opened = await nav.research("Amora, abre https://godotengine.org/", "owner-dm")
    assert opened["status"] == "opened"
    assert nav.current_delivery("owner-dm")["focus"] == "Godot Engine"
    followup = await nav.research(
        "pesquisa de novo e mim manda uma descrição", "owner-dm",
    )
    assert followup["action"] == "search"
    assert followup["status"] == "opened"
    assert queries == ["Godot Engine"]
    assert followup["query"] == "Godot Engine"
    assert all(word not in queries[0].lower() for word in ("descrição", "manda", "novo"))
    assert nav.current_delivery("owner-dm")["focus"] == "Godot Engine"
    web_events = [record.message for record in caplog.records
                  if record.name == "amora.web"]
    assert web_events == ["WEB_SEARCH_CONTEXT_RESOLVED"]
    assert not any("godotengine.org" in line or "mim manda" in line
                   for line in web_events)


@pytest.mark.asyncio
async def test_contextual_search_without_recent_same_dm_source_fails_without_web_call():
    calls = []
    nav = WebNavigator(search=lambda q: calls.append(q) or [],
                       fetch_page=lambda url: {})
    failure = await nav.research(
        "pesquisa de novo e me manda uma descrição", "new-dm",
    )
    assert failure["reason"] == "WEB_SEARCH_CONTEXT_REQUIRED"
    assert calls == []
    await nav.research("abre https://godotengine.org", "owner-dm")
    # Stub returned no opened page, so it cannot create a topic lease.
    second = await nav.research("pesquisa de novo", "owner-dm")
    assert second["reason"] == "WEB_SEARCH_CONTEXT_REQUIRED"
    assert calls == []


@pytest.mark.asyncio
async def test_contextual_search_expired_or_different_dm_cannot_leak_topic():
    nav = WebNavigator(fetch_page=lambda url: {
        "status": "opened", "url": url, "title": "Godot Engine",
        "text": "Official Godot docs", "links": [],
        "fetched_at": "2026-11-20T00:00:00+00:00",
    }, search=lambda q: (_ for _ in ()).throw(AssertionError("no search")))
    await nav.research("abre https://godotengine.org/", "owner-dm")
    assert (await nav.research("pesquisa de novo", "other-dm"))["reason"] == (
        "WEB_SEARCH_CONTEXT_REQUIRED"
    )
    old = nav.last_delivery["owner-dm"]
    nav.last_delivery["owner-dm"] = (old[0] - 901, old[1])
    assert (await nav.research("pesquisa de novo", "owner-dm"))["reason"] == (
        "WEB_SEARCH_CONTEXT_REQUIRED"
    )


@pytest.mark.asyncio
async def test_explicit_search_topic_never_inherits_previous_website():
    queries = []
    nav = WebNavigator(fetch_page=lambda url: {
        "status": "opened", "url": url, "title": "Godot Engine",
        "text": "Official Godot release notes", "links": [],
        "fetched_at": "2026-11-20T00:00:00+00:00",
    }, search=lambda q: queries.append(q) or [{
        "url": "https://blender.org/features/",
        "title": "Blender Features", "snippet": "Blender 3D software",
    }])
    await nav.research("abre https://godotengine.org/", "owner-dm")
    outcome = await nav.research(
        "pesquisa de novo sobre Blender e me manda uma descrição", "owner-dm",
    )
    assert outcome["status"] == "opened"
    assert queries == ["Blender"]
    assert outcome["query"] == "Blender"


@pytest.mark.asyncio
async def test_contextual_search_after_results_only_uses_search_subject():
    queries = []
    def search(query):
        queries.append(query)
        return [{"url": "https://godotengine.org/article/update",
                 "title": "Godot Android update notes",
                 "snippet": "Godot Android update"}]
    def blocked(url):
        raise WebFetchError("WEB_HTTP_403")
    nav = WebNavigator(search=search, fetch_page=blocked)
    first = await nav.research("pesquise Godot Android", "owner-dm")
    assert first["status"] == "results_only"
    assert nav.current_delivery("owner-dm")["focus"] == "Godot Android"
    again = await nav.research("pesquise novamente", "owner-dm")
    assert again["status"] == "results_only"
    assert queries == ["Godot Android", "Godot Android"]
