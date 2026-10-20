from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.conversation import UtteranceKind
from amora.events import PrivacyScope


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_same_turn_prefetch_never_claims_unrelated_speech(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        first = asyncio.run(
            core.conversation.enqueue_text(
                "Primeiro turno.",
                target_id="PERSON_BK",
                kind=UtteranceKind.DIALOGUE,
                priority=70,
                privacy_scope=PrivacyScope.PERSONAL,
                source_event_id=None,
                dedupe_key="prefetch-turn-a",
                metadata={"cognitive_turn_id": "turn-a", "cognitive_ordinal": 0},
            )
        )
        second = asyncio.run(
            core.conversation.enqueue_text(
                "Segundo turno.",
                target_id="PERSON_BK",
                kind=UtteranceKind.DIALOGUE,
                priority=70,
                privacy_scope=PrivacyScope.PERSONAL,
                source_event_id=None,
                dedupe_key="prefetch-turn-b",
                metadata={"cognitive_turn_id": "turn-b", "cognitive_ordinal": 0},
            )
        )
        assert first is not None and second is not None

        filtered = core.conversation.claim_next(cognitive_turn_id="turn-b")
        assert filtered is not None and filtered.id == second.id
        normal = core.conversation.claim_next()
        assert normal is not None and normal.id == first.id
    finally:
        core.close()


def test_playback_started_endpoint_is_authenticated_and_keeps_claim(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        item = asyncio.run(
            core.conversation.enqueue_text(
                "Teste de início real.",
                target_id="PERSON_BK",
                kind=UtteranceKind.DIALOGUE,
                priority=70,
                privacy_scope=PrivacyScope.PERSONAL,
                source_event_id=None,
                dedupe_key="playback-started-v12",
            )
        )
        assert item is not None
        token = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))
        headers = {"Authorization": f"Bearer {token}"}
        claimed = client.get("/v1/conversation/next", headers=headers).json()["utterance"]
        assert claimed["status"] == "claimed"

        assert client.post(f"/v1/conversation/utterances/{item.id}/started").status_code == 401
        started = client.post(
            f"/v1/conversation/utterances/{item.id}/started",
            headers=headers,
        )
        assert started.status_code == 200
        assert started.json()["utterance"]["id"] == str(item.id)
        assert started.json()["utterance"]["status"] == "claimed"
    finally:
        core.close()
