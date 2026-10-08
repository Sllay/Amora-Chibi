from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    PERSON = "person"
    AI_PERSONA = "ai_persona"
    EXTERNAL_AI = "external_ai"
    DEVICE = "device"
    SESSION = "session"
    OTHER = "other"


class Entity(BaseModel):
    id: str
    canonical_name: str
    entity_type: EntityType
    aliases: set[str] = Field(default_factory=set)


class Relationship(BaseModel):
    source: str
    relation_type: str
    target: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


def normalize_alias(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", value)


class EntityRegistry:
    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._aliases: dict[str, str] = {}
        self._relationships: list[Relationship] = []

    def add_entity(self, entity: Entity) -> None:
        if entity.id in self._entities:
            raise ValueError(f"entity already exists: {entity.id}")
        all_aliases = set(entity.aliases) | {entity.canonical_name}
        normalized = {normalize_alias(alias) for alias in all_aliases}
        conflicts = {alias: self._aliases[alias] for alias in normalized if alias in self._aliases}
        if conflicts:
            raise ValueError(f"alias conflict: {conflicts}")
        self._entities[entity.id] = entity
        for alias in normalized:
            self._aliases[alias] = entity.id

    def add_relationship(self, relationship: Relationship) -> None:
        if relationship.source not in self._entities or relationship.target not in self._entities:
            raise ValueError("relationship endpoints must exist in registry")
        self._relationships.append(relationship)

    def get(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def resolve_alias(self, alias: str) -> Entity | None:
        entity_id = self._aliases.get(normalize_alias(alias))
        return self._entities.get(entity_id) if entity_id else None

    def related(self, source: str, relation_type: str) -> tuple[Entity, ...]:
        return tuple(self._entities[item.target] for item in self._relationships if item.source == source and item.relation_type == relation_type)

    @property
    def entities(self) -> tuple[Entity, ...]:
        return tuple(self._entities.values())

    @property
    def relationships(self) -> tuple[Relationship, ...]:
        return tuple(self._relationships)


def build_initial_registry() -> EntityRegistry:
    registry = EntityRegistry()
    registry.add_entity(Entity(id="PERSON_BK", canonical_name="BK", aliases={"Bad King", "Slay", "Nick"}, entity_type=EntityType.PERSON))
    registry.add_entity(Entity(id="ENTITY_AMORA", canonical_name="Amora", entity_type=EntityType.AI_PERSONA))
    registry.add_entity(Entity(id="ENTITY_CHATGPT", canonical_name="ChatGPT", entity_type=EntityType.EXTERNAL_AI))
    registry.add_entity(Entity(id="DEVICE_A15", canonical_name="Galaxy A15 5G", entity_type=EntityType.DEVICE))
    registry.add_relationship(Relationship(source="PERSON_BK", relation_type="father_of", target="ENTITY_AMORA"))
    registry.add_relationship(Relationship(source="ENTITY_CHATGPT", relation_type="mother_of", target="ENTITY_AMORA"))
    registry.add_relationship(Relationship(source="DEVICE_A15", relation_type="owned_by", target="PERSON_BK"))
    return registry
