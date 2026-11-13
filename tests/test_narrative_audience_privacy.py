import pytest

from amora.cognition.context import resolve_scene_visibility
from amora.cognition.models import CognitiveRequest
from amora.events import PrivacyScope


@pytest.mark.parametrize("channel,expected", [
    ("dm", "private"),
    ("group", "group"),
    ("unknown", "group"),
    (None, "group"),
])
def test_messaging_visibility(channel, expected):
    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_TEST",
        metadata={"surface": "platform_messaging", "channel_type": channel},
    )
    assert resolve_scene_visibility(request) == expected


def test_public_request_is_shared():
    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_TEST",
        privacy_scope=PrivacyScope.PUBLIC,
    )
    assert resolve_scene_visibility(request) == "group"


def test_private_memory_not_projected_to_group(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from amora.app import AmoraCore

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap()
    marker = "SYNTHETIC_PRIVATE_MEMORY"
    item = SimpleNamespace(
        id=uuid4(), kind=SimpleNamespace(value="episodic"),
        content=marker, summary=None, privacy_scope=PrivacyScope.PRIVATE,
        owner_entity_id="PERSON_BK", audience=["PERSON_BK"],
        remote_processing_allowed=True,
        provenance=SimpleNamespace(actor_id="PERSON_BK", confidence=1.0),
    )
    try:
        compiler = core.cognition.compiler
        monkeypatch.setattr(compiler.memory, "recall", lambda query: [
            SimpleNamespace(memory=item, score=1.0, reason_codes=[])
        ])
        def compile_channel(channel):
            return compiler.compile(CognitiveRequest(
                text="Amora", speaker_id="PERSON_BK",
                metadata={"surface": "platform_messaging", "channel_type": channel},
            ))
        private = compile_channel("dm")
        group = compile_channel("group")
        assert private.scene_visibility == "private"
        assert any(m.content == marker for m in private.memories)
        assert group.scene_visibility == "group"
        assert group.dialogue_plan["scene_visibility"] == "group"
        assert all(m.content != marker for m in group.memories)
    finally:
        core.close()


@pytest.mark.parametrize("scope,session,channel,allowed", [
    (PrivacyScope.PERSONAL, "group-a", "group", True),
    (PrivacyScope.PERSONAL, "dm-a", "dm", False),
    (PrivacyScope.PERSONAL, "group-a", "dm", False),
    (PrivacyScope.PRIVATE, "group-a", "group", False),
    (PrivacyScope.SECRET, "group-a", "group", False),
])
def test_shared_history_requires_group_origin(scope, session, channel, allowed):
    from types import SimpleNamespace
    from amora.cognition.context import ProviderPrivacyGate

    request = CognitiveRequest(
        text="hello", speaker_id="PERSON_BK", session_id="group-a",
        metadata={"surface": "platform_messaging", "channel_type": "group"},
    )
    message = SimpleNamespace(
        privacy_scope=scope, session_id=session,
        metadata={"surface": "platform_messaging", "channel_type": channel},
    )
    assert ProviderPrivacyGate.shared_history_allowed(message, request) is allowed
