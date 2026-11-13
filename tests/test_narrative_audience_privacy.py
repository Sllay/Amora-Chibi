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


def test_derived_memory_cannot_become_public():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from uuid import uuid4
    from amora.memory.models import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind, MemoryWriteDecision
    from amora.memory.service import MemoryService

    source_id = uuid4()
    parent = SimpleNamespace(
        privacy_scope=PrivacyScope.PRIVATE, owner_entity_id="PERSON_BK",
        audience={"PERSON_BK"}, remote_processing_allowed=False,
        provenance=SimpleNamespace(actor_id="PERSON_BK"),
    )
    stored = []
    store = SimpleNamespace(get=lambda _: parent, put=stored.append)
    gate = SimpleNamespace(evaluate=lambda candidate: MemoryWriteDecision(
        store=True, score=1, threshold=0, recommended_kind=candidate.kind))
    service = MemoryService(store, SimpleNamespace(now=lambda: datetime.now(timezone.utc)), gate)
    candidate = MemoryCandidate(
        kind=MemoryKind.EPISODIC, content="synthetic derived summary",
        privacy_scope=PrivacyScope.PUBLIC,
        provenance=MemoryProvenance(kind=ProvenanceKind.DERIVED, source="test",
                                    derived_from=[source_id]),
    )
    result = service.remember(candidate)
    assert result.memory.privacy_scope == PrivacyScope.PRIVATE
    assert result.memory.audience == {"PERSON_BK"}
    assert not result.memory.remote_processing_allowed
    assert len(stored) == 1


def test_unlabeled_relationship_is_not_projected_to_shared_audience():
    from amora.identity.models import Entity, EntityRegistry, EntityType, Relationship
    from amora.language.text_references import scoped_registry

    registry = EntityRegistry()
    for entity_id in ("PERSON_SYNTHETIC_A", "PERSON_SYNTHETIC_B"):
        registry.add_entity(Entity(id=entity_id, canonical_name=entity_id,
                                   entity_type=EntityType.PERSON))
    edge = Relationship(source="PERSON_SYNTHETIC_A", relation_type="colleague_of",
                        target="PERSON_SYNTHETIC_B")
    registry.add_relationship(edge)
    key = (edge.source, edge.relation_type, edge.target)
    assert scoped_registry(registry, edge.source).relationships == (edge,)
    assert not scoped_registry(registry, edge.source, include_recipient_edges=False).relationships
    assert scoped_registry(registry, edge.source, include_recipient_edges=False,
                           authorized_relations={key}).relationships == (edge,)
