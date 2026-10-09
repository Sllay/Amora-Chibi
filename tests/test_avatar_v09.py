import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.avatar import AvatarProject, AvatarStore
from amora.conversation import UtteranceKind
from amora.events import AmoraEvent, PrivacyScope
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc))


def test_avatar_project_and_asset_survive_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        project = first.avatar.project()
        project.name = "Amora — papis custom"
        project.color_slots[0].value = "#e8bcbc"
        first.avatar.save_project(project)
        info = first.avatar.store.put_asset("hair_front", "image/png", b"\x89PNG\r\n\x1a\nfixture")
        assert info.id == "hair_front"
        assert first.health_report()["schema_version"] == 9
    finally:
        first.close()

    second = AmoraCore.bootstrap(clock=clock)
    try:
        restored = second.avatar.project()
        assert restored.name == "Amora — papis custom"
        assert restored.color_slots[0].value == "#e8bcbc"
        asset = second.avatar.store.read_asset("hair_front")
        assert asset is not None
        assert asset[1].startswith(b"\x89PNG")
        assert second.avatar.store.asset_count() == 1
    finally:
        second.close()


def test_mind_to_avatar_semantic_action_and_speaking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        event = AmoraEvent(
            event_type="action.semantic_request",
            source="test",
            actor="ENTITY_AMORA",
            target="PERSON_BK",
            payload={"action_kind": "initiate_contact"},
            privacy_scope=PrivacyScope.PERSONAL,
        )
        asyncio.run(core.avatar.handle_semantic_event(event))
        state = core.avatar.semantic_state()
        assert state.gesture == "attention"
        assert state.attention_target == "PERSON_BK"
        assert "ACTION_INITIATE_CONTACT" in state.reason_codes

        item = asyncio.run(core.conversation.enqueue_text(
            "Papis?",
            target_id="PERSON_BK",
            kind=UtteranceKind.SEMANTIC,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=event.id,
            dedupe_key="avatar-speaking",
        ))
        assert item is not None
        claimed = core.conversation.claim_next()
        assert claimed is not None
        speaking = core.avatar.semantic_state()
        assert speaking.speaking is True
        assert speaking.mouth == "talk"
    finally:
        core.close()


def test_avatar_api_project_asset_and_static_studio(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        token = core.secrets.get("LOCAL_API_TOKEN")
        assert token
        client = TestClient(create_api(core))
        headers = {"Authorization": f"Bearer {token}"}

        project = client.get("/v1/avatar/project", headers=headers)
        assert project.status_code == 200
        body = project.json()
        body["name"] = "Amora Studio API"
        saved = client.put("/v1/avatar/project", headers=headers, json=body)
        assert saved.status_code == 200
        assert saved.json()["name"] == "Amora Studio API"

        uploaded = client.put(
            "/v1/avatar/assets/test_face",
            headers={**headers, "Content-Type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nasset",
        )
        assert uploaded.status_code == 200
        fetched = client.get("/v1/avatar/assets/test_face", headers=headers)
        assert fetched.status_code == 200
        assert fetched.content.startswith(b"\x89PNG")

        state = client.get("/v1/avatar/state", headers=headers)
        assert state.status_code == 200
        assert state.json()["expression"] in {"neutral", "happy", "sad", "angry"}

        studio = client.get("/avatar/")
        assert studio.status_code == 200
        assert "Amora Avatar" in studio.text
    finally:
        core.close()


def test_backup_restore_preserves_avatar_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        project = core.avatar.project()
        project.metadata["acceptance"] = "v09"
        core.avatar.save_project(project)
        backup = core.create_backup("v09-avatar.db")
    finally:
        core.close()

    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db")
    restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok"
        assert restored_db.schema_version() == 9
    finally:
        restored_db.close()

    store = AvatarStore(restored_path, tmp_path / "restored-assets", clock)
    try:
        restored = store.get_project()
        assert restored.metadata["acceptance"] == "v09"
    finally:
        store.close()
