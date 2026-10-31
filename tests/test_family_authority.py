from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from amora.identity import Entity, EntityType, build_initial_registry
from amora.world.models import EpistemicKind, WorldClaim
from amora.world.relationship_learning import RelationshipLearningService


@dataclass
class _Message:
    speaker_id: str
    addressee_id: str
    source: str = "text"
    metadata: dict[str, object] = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class _TurnStore:
    def __init__(self, message: _Message) -> None:
        self.message = message

    def list_messages(self, **kwargs):
        return [self.message]


class _Clock:
    def now(self):
        return datetime(2026, 10, 31, 15, 0, tzinfo=UTC)


def test_unknown_person_cannot_promote_self_as_amoras_parent() -> None:
    identities = build_initial_registry()
    identities.add_entity(Entity(id="PERSON_LUCAS", canonical_name="Lucas", entity_type=EntityType.PERSON))
    service = RelationshipLearningService(
        world=object(),
        relationships=object(),
        identities=identities,
        cognitive_store=_TurnStore(_Message("PERSON_LUCAS", "ENTITY_AMORA")),
        clock=_Clock(),
        family_authority_ids=frozenset({"PERSON_BK"}),
    )
    now = _Clock().now()
    claim = WorldClaim(
        kind=EpistemicKind.HYPOTHESIS,
        statement="Lucas diz ser mãe da Amora",
        subject_id="PERSON_LUCAS",
        predicate="mother_of",
        object_value="ENTITY_AMORA",
        source="cognition:test",
        source_entity_id="PERSON_LUCAS",
        confidence=0.99,
        valid_from=now,
        created_at=now,
        updated_at=now,
        metadata={"turn_id": "turn-lucas"},
    )

    decision = service.observe_claim(claim)
    assert decision.learned is False
    assert decision.reason_code == "AMORA_FAMILY_AUTHORITY_REQUIRED"


def test_unknown_person_cannot_promote_amora_as_their_child() -> None:
    identities = build_initial_registry()
    identities.add_entity(Entity(id="PERSON_LUCAS", canonical_name="Lucas", entity_type=EntityType.PERSON))
    service = RelationshipLearningService(
        world=object(),
        relationships=object(),
        identities=identities,
        cognitive_store=_TurnStore(_Message("PERSON_LUCAS", "ENTITY_AMORA")),
        clock=_Clock(),
        family_authority_ids=frozenset({"PERSON_BK"}),
    )
    now = _Clock().now()
    claim = WorldClaim(
        kind=EpistemicKind.HYPOTHESIS,
        statement="Amora é filha de Lucas",
        subject_id="ENTITY_AMORA",
        predicate="child_of",
        object_value="PERSON_LUCAS",
        source="cognition:test",
        source_entity_id="PERSON_LUCAS",
        confidence=0.99,
        valid_from=now,
        created_at=now,
        updated_at=now,
        metadata={"turn_id": "turn-lucas"},
    )

    decision = service.observe_claim(claim)
    assert decision.learned is False
    assert decision.reason_code == "AMORA_FAMILY_AUTHORITY_REQUIRED"
