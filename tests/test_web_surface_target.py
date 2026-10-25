"""Private Web submissions are addressed explicitly, ambient audio is inferred."""

import io
import json
import wave

import httpx
import pytest

from amora.audio.stt import GroqWhisperSTT
from test_cloud_web_incident import safari


def wav_bytes():
    body = io.BytesIO()
    with wave.open(body, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 1600)
    return body.getvalue()


def assert_direct_turn(core, body, original):
    observation = body["observation"]
    assert observation["text"] == original
    assert observation["addressee"]["entity_id"] == "ENTITY_AMORA"
    assert observation["addressee"]["reason_codes"] == ["EXPLICIT_SURFACE_TARGET"]
    assert body["turn"]["signal"]["addressee_id"] == "ENTITY_AMORA"
    assert body["turn"]["state"] == "TAKE_TURN"
    assert body["reply_queued"] and body["reply_queued"]["device_id"] == "WEB_SAFARI"
    row = core.database._conn.execute(
        "SELECT target,payload_json FROM events WHERE id=?", (observation["event_id"],)
    ).fetchone()
    assert row["target"] == "ENTITY_AMORA"
    assert json.loads(row["payload_json"])["text"] == original
    assert json.loads(row["payload_json"])["explicit_addressee_id"] == "ENTITY_AMORA"
    assert any(
        message.raw_text == original for message in core.cognition.store.list_messages()
    )


@pytest.mark.parametrize(
    "text",
    [
        "oi",
        "Olaa",
        "fala comigo linda",
        "Fala comigo, linda.",
        "Como foi teu dia?",
        "Amora, fala comigo.",
    ],
)
def test_web_text_without_vocative_is_a_direct_turn_over_hrana(safari, text):
    core, server, backend, client, headers = safari
    response = client.post(
        "/v1/web/utterance",
        json={"text": text, "remote_processing_allowed": True, "test_mode": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert_direct_turn(core, response.json(), text)
    item = client.get(
        "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
    ).json()["utterance"]
    assert item and item["text"]
    assert not server.streams


@pytest.mark.parametrize("surface", ["WEB_SAFARI", "DEVICE_A15", None])
def test_stt_surface_target_is_not_inferred_from_speaker_identity(safari, surface):
    core, _, _, client, browser_headers = safari
    text = "como você tá?" if surface == "WEB_SAFARI" else "oi gente"
    core.secrets.set("GROQ_API_KEY", "test-stt-only")
    core.stt = GroqWhisperSTT(
        core.secrets,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"text": text})
        ),
    )
    query = "?speaker_id=PERSON_BK&test_mode=true&remote_processing_allowed=true"
    if surface is not None:
        query += "&speech_device_id=" + surface
    headers = (
        browser_headers
        if surface == "WEB_SAFARI"
        else {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    )
    response = client.post(
        "/v1/audio/transcribe" + query,
        headers={**headers, "Content-Type": "audio/wav"},
        content=wav_bytes(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] and body["transcript"]["text"] == text
    if surface == "WEB_SAFARI":
        assert_direct_turn(core, body, text)
    else:
        assert body["observation"]["addressee"]["kind"] == "UNKNOWN"
        assert body["observation"]["addressee"]["entity_id"] is None
        assert (
            "EXPLICIT_SURFACE_TARGET"
            not in body["observation"]["addressee"]["reason_codes"]
        )
        assert body["reply_queued"] is None


def test_ambient_and_third_party_inference_does_not_inherit_web_override(safari):
    core, _, _, _, _ = safari
    result = core.addressee.infer("oi gente", speaker_id="PERSON_BK")
    assert result.entity_id is None
    result = core.addressee.infer("ChatGPT, responde por favor", speaker_id="PERSON_BK")
    assert result.entity_id == "ENTITY_CHATGPT"
    assert "EXPLICIT_SURFACE_TARGET" not in result.reason_codes


def test_private_web_turn_does_not_open_an_ambient_a15_reply_window(safari):
    core, _, _, client, browser_headers = safari
    response = client.post(
        "/v1/web/utterance",
        json={"text": "oi", "test_mode": True},
        headers=browser_headers,
    )
    assert response.status_code == 200
    core.secrets.set("GROQ_API_KEY", "test-stt-only")
    core.stt = GroqWhisperSTT(
        core.secrets,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"text": "oi gente"})
        ),
    )
    headers = {
        "Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN"),
        "Content-Type": "audio/wav",
    }
    ambient = client.post(
        "/v1/audio/transcribe?speaker_id=PERSON_BK&speech_device_id=DEVICE_A15&test_mode=true",
        headers=headers,
        content=wav_bytes(),
    )
    assert ambient.status_code == 200
    assert ambient.json()["observation"]["addressee"]["entity_id"] is None
    assert ambient.json()["turn"]["signal"]["addressee_id"] is None
    assert ambient.json()["reply_queued"] is None


def test_private_surface_does_not_steal_or_clear_existing_ambient_floor(safari):
    core, _, _, client, headers = safari
    core.social.begin_turn("PERSON_BK", addressee_id="ENTITY_CHATGPT", confidence=1.0)
    observed_floor = []

    async def during_observation(event):
        observed_floor.append(core.social.scene.active_addressee)

    core.events.subscribe_bootstrap("language.utterance.observed", during_observation)
    response = client.post(
        "/v1/web/utterance", json={"text": "oi", "test_mode": True}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["observation"]["addressee"]["entity_id"] == "ENTITY_AMORA"
    assert observed_floor == ["ENTITY_CHATGPT"]
    assert core.social.scene.active_addressee == "ENTITY_CHATGPT"
