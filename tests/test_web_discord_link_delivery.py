"""Discord web links are Core-owned, even if model realizes fallback text."""
from __future__ import annotations

import asyncio

import pytest

from amora.messaging.web_delivery import (
    compose_web_delivery, requested_screenshot_source, screenshot_requested,
    neutralize_screenshot_claims,
)
from amora.web_tools.navigator import WebIntent, WebNavigator, parse_web_intent


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
