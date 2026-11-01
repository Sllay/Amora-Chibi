from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amora.time import Clock

from .models import Entity, EntityRegistry, EntityType, Relationship


class PlatformKind(StrEnum):
    WEB = "web"
    WHATSAPP = "whatsapp"
    DISCORD = "discord"


class BindingTrust(StrEnum):
    OPERATOR_VERIFIED = "operator_verified"
    PLATFORM_AUTHENTICATED = "platform_authenticated"
    OBSERVED = "observed"


_DISCORD_SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")


class PersonProfileSeed(BaseModel):
    """Small operator-owned bootstrap surface for one canonical person.

    This is deliberately not a second memory store. Stable facts and explicit
    operator capabilities live here; learned preferences, episodes, beliefs and
    affect continue to belong to World/Memory/Emotion.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(pattern=r"^PERSON_[A-Z0-9_]+$", min_length=8, max_length=96)
    canonical_name: str = Field(min_length=1, max_length=120)
    aliases: set[str] = Field(default_factory=set, max_length=32)
    birth_date: date | None = None
    age_years: int | None = Field(default=None, ge=0, le=150)
    short_description: str | None = Field(default=None, max_length=600)
    can_define_amora_family: bool = False
    can_control_platform_presence: bool = False

    @model_validator(mode="after")
    def validate_age_source(self) -> "PersonProfileSeed":
        if self.birth_date is not None and self.age_years is not None:
            raise ValueError("use birth_date or age_years, not both")
        return self


class SeedRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=96)
    relation_type: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=96)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PeopleProfileCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=16)
    revision: str = Field(min_length=1, max_length=128)
    profiles: list[PersonProfileSeed] = Field(min_length=1, max_length=128)
    relationships: list[SeedRelationship] = Field(default_factory=list, max_length=512)

    @model_validator(mode="after")
    def validate_catalog(self) -> "PeopleProfileCatalog":
        ids = [profile.entity_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("people profile entity_ids must be unique")
        known = set(ids) | {"ENTITY_AMORA", "ENTITY_CHATGPT", "DEVICE_A15"}
        for relationship in self.relationships:
            if relationship.source not in known or relationship.target not in known:
                raise ValueError("seed relationship endpoints must be known catalog/core entities")
        return self


class PlatformIdentityBinding(BaseModel):
    """Private mapping from a stable native platform id to a canonical person.

    Identity bindings are authority, not aliases. A mutable username/display name
    must never be used as the authoritative Discord key; those values remain
    presentation metadata/aliases while ``discord_user_id`` is the immutable
    snowflake emitted by the Discord adapter.
    """

    model_config = ConfigDict(extra="forbid")

    platform: PlatformKind
    identifier_type: str = Field(min_length=1, max_length=64)
    external_actor_id: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(pattern=r"^PERSON_[A-Z0-9_]+$", min_length=8, max_length=96)
    account_scope: str = Field(default="primary", min_length=1, max_length=96)
    trust: BindingTrust = BindingTrust.OPERATOR_VERIFIED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_native_identifier(self) -> "PlatformIdentityBinding":
        if self.platform == PlatformKind.DISCORD and self.identifier_type in {"discord_user_id", "user_id"}:
            if not _DISCORD_SNOWFLAKE.fullmatch(self.external_actor_id.strip()):
                raise ValueError(
                    "Discord identity requires the numeric user snowflake, not username/display name"
                )
        return self


class IdentityBindingsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=16)
    bindings: list[PlatformIdentityBinding] = Field(default_factory=list, max_length=512)


class PersonProfileStore(Protocol):
    def upsert_person_profile(self, profile: PersonProfileSeed, *, source_revision: str, updated_at) -> None: ...
    def get_person_profile(self, entity_id: str) -> PersonProfileSeed | None: ...
    def list_person_profiles(self) -> tuple[PersonProfileSeed, ...]: ...
    def upsert_platform_identity_binding(self, binding: PlatformIdentityBinding, *, updated_at) -> None: ...
    def resolve_platform_identity_binding(
        self,
        *,
        platform: str,
        identifier_type: str,
        external_actor_id: str,
        account_scope: str,
    ) -> str | None: ...
    def platform_bindings_for(self, entity_id: str) -> tuple[PlatformIdentityBinding, ...]: ...


class PeopleProfileCatalogStore:
    """Load the editable public bootstrap catalog and fingerprint exact bytes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[3] / "config" / "people_profiles.json"

    def load(self) -> tuple[PeopleProfileCatalog, str]:
        raw = self.path.read_bytes()
        catalog = PeopleProfileCatalog.model_validate_json(raw)
        return catalog, hashlib.sha256(raw).hexdigest()


class PersonProfileService:
    def __init__(
        self,
        *,
        store: PersonProfileStore,
        identities: EntityRegistry,
        clock: Clock,
        persist_registry,
    ) -> None:
        self.store = store
        self.identities = identities
        self.clock = clock
        self.persist_registry = persist_registry
        self.catalog_revision: str | None = None
        self.catalog_fingerprint: str | None = None
        self.private_binding_import_count = 0

    @staticmethod
    def _entity_from_profile(profile: PersonProfileSeed) -> Entity:
        return Entity(
            id=profile.entity_id,
            canonical_name=profile.canonical_name,
            aliases=set(profile.aliases),
            entity_type=EntityType.PERSON,
        )

    @staticmethod
    def _identifier_type_candidates(platform: str, identifier_type: str) -> tuple[str, ...]:
        """Return exact-first identifier aliases accepted at the Core boundary.

        The early People Web UI stored Discord snowflakes as ``user_id`` while
        the Discord worker contract emits ``discord_user_id``. Both represent the
        same native Discord user id. Keep the fallback scoped to Discord so old
        operator-owned *numeric* bindings remain valid without ever treating a
        mutable username as identity authority.
        """
        if platform != PlatformKind.DISCORD.value:
            return (identifier_type,)
        if identifier_type == "discord_user_id":
            return ("discord_user_id", "user_id")
        if identifier_type == "user_id":
            return ("user_id", "discord_user_id")
        return (identifier_type,)

    def apply_catalog(self, catalog: PeopleProfileCatalog, *, fingerprint: str | None = None) -> None:
        now = self.clock.now()
        persisted = {profile.entity_id: profile for profile in self.store.list_person_profiles()}
        first_profile_bootstrap = not persisted
        newly_seeded: set[str] = set()

        for seed in catalog.profiles:
            effective = persisted.get(seed.entity_id)
            if effective is None:
                effective = seed
                newly_seeded.add(seed.entity_id)
            self.identities.upsert_entity(self._entity_from_profile(effective))

        for item in catalog.relationships:
            should_seed = first_profile_bootstrap or item.source in newly_seeded or item.target in newly_seeded
            if not should_seed:
                continue
            self.identities.upsert_relationship(
                Relationship(
                    source=item.source,
                    relation_type=item.relation_type,
                    target=item.target,
                    confidence=item.confidence,
                )
            )

        self.persist_registry(self.identities)
        for seed in catalog.profiles:
            if seed.entity_id in newly_seeded:
                self.store.upsert_person_profile(seed, source_revision=catalog.revision, updated_at=now)

        self.catalog_revision = catalog.revision
        self.catalog_fingerprint = fingerprint

    def upsert_profile(
        self,
        profile: PersonProfileSeed,
        *,
        source_revision: str = "operator:web",
        allow_platform_presence_authority_change: bool = False,
    ) -> PersonProfileSeed:
        current_entity = self.identities.get(profile.entity_id)
        if current_entity is not None and current_entity.entity_type != EntityType.PERSON:
            raise ValueError("profile entity_id belongs to a non-person entity")

        persisted = self.store.get_person_profile(profile.entity_id)
        field_was_explicit = "can_control_platform_presence" in profile.model_fields_set
        if persisted is not None:
            if not field_was_explicit:
                profile = profile.model_copy(update={"can_control_platform_presence": persisted.can_control_platform_presence})
            elif (
                profile.can_control_platform_presence != persisted.can_control_platform_presence
                and not allow_platform_presence_authority_change
            ):
                raise ValueError("platform presence authority change requires dedicated confirmed mutation")
        elif profile.can_control_platform_presence and not allow_platform_presence_authority_change:
            raise ValueError("platform presence authority grant requires dedicated confirmed mutation")

        self.identities.upsert_entity(self._entity_from_profile(profile))
        self.persist_registry(self.identities)
        self.store.upsert_person_profile(profile, source_revision=source_revision, updated_at=self.clock.now())
        return profile

    def upsert_binding(self, binding: PlatformIdentityBinding) -> PlatformIdentityBinding:
        entity = self.identities.get(binding.entity_id)
        if entity is None or entity.entity_type != EntityType.PERSON:
            raise ValueError(f"binding references unknown person: {binding.entity_id}")
        self.store.upsert_platform_identity_binding(binding, updated_at=self.clock.now())
        return binding

    def import_private_bindings(self, path: Path) -> int:
        if not path.is_file():
            return 0
        raw = path.read_bytes()
        payload = IdentityBindingsFile.model_validate_json(raw)
        imported = 0
        for binding in payload.bindings:
            self.upsert_binding(binding)
            imported += 1
        self.private_binding_import_count += imported
        return imported

    def import_configured_private_bindings(self, *, default_path: Path) -> int:
        configured = os.getenv("AMORA_IDENTITY_BINDINGS_FILE", "").strip()
        return self.import_private_bindings(Path(configured).expanduser() if configured else default_path)

    def resolve_actor(
        self,
        *,
        platform: PlatformKind | str,
        identifier_type: str,
        external_actor_id: str,
        account_scope: str = "primary",
    ) -> Entity | None:
        platform_value = str(getattr(platform, "value", platform))
        # Runtime Discord identity is authoritative only when the adapter supplied
        # the immutable numeric snowflake. A username accidentally stored in the
        # old user_id field can no longer impersonate a canonical person.
        if (
            platform_value == PlatformKind.DISCORD.value
            and identifier_type in {"discord_user_id", "user_id"}
            and not _DISCORD_SNOWFLAKE.fullmatch(external_actor_id.strip())
        ):
            return None
        for candidate in self._identifier_type_candidates(platform_value, identifier_type):
            entity_id = self.store.resolve_platform_identity_binding(
                platform=platform_value,
                identifier_type=candidate,
                external_actor_id=external_actor_id,
                account_scope=account_scope,
            )
            if entity_id:
                return self.identities.get(entity_id)
        return None

    def profile(self, entity_id: str) -> PersonProfileSeed | None:
        return self.store.get_person_profile(entity_id)

    def profiles(self) -> tuple[PersonProfileSeed, ...]:
        return self.store.list_person_profiles()

    def bindings_for(self, entity_id: str) -> tuple[PlatformIdentityBinding, ...]:
        return self.store.platform_bindings_for(entity_id)

    def family_authorities(self) -> frozenset[str]:
        return frozenset(profile.entity_id for profile in self.store.list_person_profiles() if profile.can_define_amora_family)

    def platform_presence_authorities(self) -> frozenset[str]:
        return frozenset(profile.entity_id for profile in self.store.list_person_profiles() if profile.can_control_platform_presence)

    def can_control_platform_presence(self, entity_id: str | None) -> bool:
        return bool(entity_id and entity_id in self.platform_presence_authorities())

    def state(self) -> dict[str, object]:
        profiles = self.profiles()
        bindings = sum(len(self.store.platform_bindings_for(profile.entity_id)) for profile in profiles)
        return {
            "person_profile_count": len(profiles),
            "platform_identity_binding_count": bindings,
            "people_profile_revision": self.catalog_revision,
            "people_profile_fingerprint": self.catalog_fingerprint,
            "private_binding_import_count": self.private_binding_import_count,
            "family_authority_ids": sorted(self.family_authorities()),
            "platform_presence_authority_ids": sorted(self.platform_presence_authorities()),
        }


def redact_binding(binding: PlatformIdentityBinding) -> dict[str, object]:
    digest = hashlib.sha256(binding.external_actor_id.encode("utf-8")).hexdigest()[:12]
    return {
        "platform": binding.platform.value,
        "identifier_type": binding.identifier_type,
        "external_actor_hash": digest,
        "entity_id": binding.entity_id,
        "account_scope": binding.account_scope,
        "trust": binding.trust.value,
        "confidence": binding.confidence,
        "enabled": binding.enabled,
    }


def load_bindings_text(text: str) -> IdentityBindingsFile:
    return IdentityBindingsFile.model_validate(json.loads(text))