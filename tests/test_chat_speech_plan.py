"""Turn presentation is independent of vocal segmentation and delivery."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition import SpeechCue, SpeechNormalizer
from amora.conversation import UtteranceKind
from amora.events import PrivacyScope
from amora.voice.backend import (
    VoiceChunk,
    VoiceSession,
    validated_chunks,
    StreamingVoiceBackend,
)
import test_cloud_web_incident
from test_v10_dialogue_emotion_integration import StructuredProvider

safari = test_cloud_web_incident.safari


@pytest.mark.parametrize(
    "display,speech,cue",
    [
        ("kkkk, pai...", "pai.", SpeechCue.LAUGHTER),
        ("hahaha, pai...", "pai.", SpeechCue.LAUGHTER),
        ("rsrsrsrs, né.", "né.", SpeechCue.LAUGHTER),
        ("😂 tu é impossível", "tu é impossível", SpeechCue.LAUGHTER),
        ("(suspiro), tá bom.", "tá bom.", SpeechCue.SIGH),
        ("😮, nossa!", "nossa!", SpeechCue.SURPRISE),
        ("😤, de novo.", "de novo.", SpeechCue.DISSATISFACTION),
        ("😮‍💨 tá.", "tá.", SpeechCue.SIGH),
    ],
)
def test_immutable_display_and_semantic_cues(display, speech, cue):
    plan = SpeechNormalizer().compile(
        display, turn_id=uuid4(), privacy_scope=PrivacyScope.PERSONAL
    )
    assert plan.display_text == display and plan.speech_text == speech
    assert cue in plan.cues
    assert plan.cue_support()["unsupported"] == [cue.value]
    assert plan.cue_support(frozenset({cue}))["supported"] == [cue.value]
    with pytest.raises(FrozenInstanceError):
        plan.display_text = "changed"


class TurnProvider(StructuredProvider):
    text = "kkkk 😂, pai. Tu é impossível. Vou ficar por aqui."

    async def realize(self, context):
        return {
            "moves": [
                {"kind": context.dialogue_plan["response_moves"][0], "text": self.text}
            ],
            "closing": "statement",
        }


def test_unknown_nonverbal_markers_are_explicitly_unsupported_not_discarded():
    display = "(sorri) 💜, pai."
    plan = SpeechNormalizer().compile(
        display, turn_id=uuid4(), privacy_scope=PrivacyScope.PERSONAL
    )
    assert plan.display_text == display and plan.speech_text == "pai."
    assert plan.cues == ()
    assert plan.cue_support()["unsupported_markers"] == ["(sorri)", "💜"]


def test_remote_realization_three_segments_preserves_full_turn_and_restart(
    safari, tmp_path, monkeypatch
):
    core, server, backend, client, headers = safari
    core.cognition.register_provider(TurnProvider())
    response = client.post(
        "/v1/web/utterance",
        headers=headers,
        json={
            "text": "Fala comigo, linda.",
            "remote_processing_allowed": True,
            "test_mode": True,
        },
    )
    assert response.status_code == 200, response.text
    items = [
        client.get(
            "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
        ).json()["utterance"]
        for _ in range(3)
    ]
    assert all(items), items
    turn = items[0]["metadata"]["cognitive_turn_id"]
    assert {item["metadata"]["cognitive_turn_id"] for item in items} == {turn}
    assert {item["display_text"] for item in items} == {TurnProvider.text}
    assert all(
        "kkkk" not in item["text"] and "😂" not in item["text"] for item in items
    )
    assert "LAUGHTER" in items[0]["metadata"]["unsupported_cues"]
    assert len(core.cognition.store.list_segments(turn)) == 3

    cancelled = client.post(
        f"/v1/conversation/utterances/{items[0]['id']}/cancel",
        headers=headers,
        json={"reason": "web_muted", "speech_only": True},
    )
    assert (
        cancelled.status_code == 200 and cancelled.json()["cancelled_utterances"] == 3
    )
    assert not core.cognition.is_turn_cancelled(turn)
    assert (
        client.get(
            "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
        ).json()["utterance"]
        is None
    )
    assert (
        client.get(
            f"/v1/web/voice/utterances/{items[0]['id']}/audio", headers=headers
        ).status_code
        == 409
    )
    assert all(
        core.conversation.store.get_utterance(item["id"]).display_text
        == TurnProvider.text
        for item in items
    )
    assert core.cognition.store.list_segments(turn)[0].status.value == "interrupted"
    # A stale player callback must not rewrite cancelled delivery as spoken.
    client.post(
        f"/v1/conversation/utterances/{items[0]['id']}/started", headers=headers
    )
    client.post(
        f"/v1/conversation/utterances/{items[0]['id']}/ack",
        headers=headers,
        json={"spoken": True},
    )
    assert core.cognition.store.list_segments(turn)[0].status.value == "interrupted"
    monkeypatch.setenv(
        "AMORA_SECRET_LOCAL_API_TOKEN", core.secrets.get("LOCAL_API_TOKEN")
    )
    core.close()
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    restarted = AmoraCore.bootstrap(storage=backend)
    try:
        assert restarted.conversation.claim_next("WEB_SAFARI") is None
        assert (
            restarted.conversation.store.get_utterance(items[0]["id"]).display_text
            == TurnProvider.text
        )
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_voice_only_cancellation_suppresses_late_segments_without_cancelling_cognition(
    safari,
):
    core, _, _, _, _ = safari
    turn = uuid4()

    async def enqueue(ordinal):
        return await core.conversation.enqueue_text(
            "fala",
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key=f"speech:cognitive:{turn}:{ordinal}",
            device_id="WEB_SAFARI",
            metadata={"cognitive_turn_id": str(turn), "display_text": "texto completo"},
        )

    first = await enqueue(0)
    await core.conversation.cancel_utterance(
        first.id, speech_only=True, reason="web_manual_stop"
    )
    late = await enqueue(1)
    assert late.status.value == "cancelled"
    assert late.display_text == "texto completo"
    assert core.conversation.claim_next("WEB_SAFARI") is None
    assert not core.cognition.is_turn_cancelled(turn)


@pytest.mark.asyncio
async def test_muting_during_enqueue_does_not_cancel_response_production(
    safari, monkeypatch
):
    core, _, _, _, _ = safari
    core.cognition.register_provider(TurnProvider())
    enqueue = core.conversation.enqueue_text
    items = []

    async def mute_after_first(*args, **kwargs):
        item = await enqueue(*args, **kwargs)
        items.append(item)
        if len(items) == 1:
            await core.conversation.cancel_utterance(
                item.id, speech_only=True, reason="web_muted"
            )
        return item

    monkeypatch.setattr(core.conversation, "enqueue_text", mute_after_first)
    _, reply = await core.conversation.observe_completed_utterance(
        "Fala comigo, linda.",
        speaker_id="PERSON_BK",
        addressee_id="ENTITY_AMORA",
        source_event_id=None,
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"speech_device_id": "WEB_SAFARI", "test_mode": True},
    )
    assert reply is not None and len(items) == 3
    turn = reply.metadata["cognitive_turn_id"]
    assert not core.cognition.is_turn_cancelled(turn)
    assert all(item.display_text == TurnProvider.text for item in items)
    assert all(
        core.conversation.store.get_utterance(item.id).status.value == "cancelled"
        for item in items
    )
    assert all(
        segment.status.value == "interrupted"
        for segment in core.cognition.store.list_segments(turn)
    )
    assert (
        core.cognition.state()["last_realization"]["frame"]["moves"][0]["text"]
        == TurnProvider.text
    )


def test_cue_only_response_is_visible_without_tts(safari):
    core, _, _, client, headers = safari
    provider = TurnProvider()
    provider.text = "😂"
    core.cognition.register_provider(provider)
    response = client.post(
        "/v1/web/utterance",
        headers=headers,
        json={
            "text": "Fala comigo, linda.",
            "remote_processing_allowed": True,
            "test_mode": True,
        },
    )
    assert response.status_code == 200, response.text
    item = client.get(
        "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
    ).json()["utterance"]
    assert item["display_text"] == "😂" and item["text"] == ""
    assert (
        client.get(
            f"/v1/web/voice/utterances/{item['id']}/audio", headers=headers
        ).status_code
        == 409
    )


def test_legacy_barge_in_still_cancels_cognition(safari):
    core, _, _, client, headers = safari
    response = client.post("/v1/web/utterance", headers=headers, json={"text": "oi"})
    item = response.json()["reply_queued"]
    client.post(
        f"/v1/conversation/utterances/{item['id']}/cancel",
        headers=headers,
        json={"reason": "barge_in"},
    )
    assert core.cognition.is_turn_cancelled(item["metadata"]["cognitive_turn_id"])


def test_cue_only_prefix_does_not_suppress_later_spoken_content(safari):
    core, _, _, client, headers = safari
    provider = TurnProvider()
    provider.text = "kkkkkkkkkkkk. Tu é impossível. Vou ficar por aqui."
    core.cognition.register_provider(provider)
    response = client.post(
        "/v1/web/utterance",
        headers=headers,
        json={
            "text": "Fala comigo, linda.",
            "remote_processing_allowed": True,
            "test_mode": True,
        },
    )
    assert response.status_code == 200, response.text
    item = client.get(
        "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
    ).json()["utterance"]
    assert item["display_text"] == provider.text
    assert item["text"] == "Tu é impossível."


def test_optional_voice_stream_contract_and_resource_cleanup():
    session = VoiceSession(uuid4(), uuid4(), uuid4())
    closed = []

    def chunks():
        try:
            yield VoiceChunk(session, 0, b"\0\0" * 160, 16000, 1)
            yield VoiceChunk(session, 1, b"\0\0" * 160, 16000, 1, True)
        finally:
            closed.append(True)

    stream = validated_chunks(session, chunks())
    assert [chunk.sequence for chunk in stream] == [0, 1]
    assert closed == [True]

    class WavOnly:
        def state(self):
            return {}

        def render(self, item):
            raise NotImplementedError

    assert not isinstance(WavOnly(), StreamingVoiceBackend)
    stream = validated_chunks(session, chunks())
    next(stream)
    stream.close()
    assert closed == [True, True]


@pytest.mark.parametrize(
    "mode", ["sequence", "identity", "format", "incomplete", "after_final"]
)
def test_voice_stream_rejects_discontinuity(mode):
    session = VoiceSession(uuid4(), uuid4(), uuid4())
    first = VoiceChunk(session, 0, b"\0\0", 16000, 1, mode == "after_final")
    second = VoiceChunk(
        VoiceSession(uuid4(), uuid4(), uuid4()) if mode == "identity" else session,
        2 if mode == "sequence" else 1,
        b"\0\0",
        24000 if mode == "format" else 16000,
        1,
        mode != "incomplete",
    )
    with pytest.raises(ValueError, match="VOICE_STREAM_"):
        list(validated_chunks(session, iter([first, second])))
