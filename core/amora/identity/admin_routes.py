from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from uuid import uuid4

from fastapi import Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from amora.identity.models import Entity, EntityRegistry, EntityType, Relationship, normalize_alias
from amora.identity.profiles import (
    BindingTrust,
    IdentityBindingsFile,
    PeopleProfileCatalog,
    PersonProfileSeed,
    PlatformIdentityBinding,
    PlatformKind,
    SeedRelationship,
)


FAMILY_RELATION_TYPES = frozenset(
    {
        "father_of",
        "mother_of",
        "child_of",
        "sibling_of",
        "cousin_of",
        "uncle_of",
        "aunt_of",
        "nephew_of",
        "niece_of",
        "grandfather_of",
        "grandmother_of",
        "grandchild_of",
    }
)
SYMMETRIC_RELATION_TYPES = frozenset({"sibling_of", "cousin_of"})


class ProfileMutation(BaseModel):
    profile: PersonProfileSeed
    confirm_family_authority_change: bool = False


class RelationshipMutation(BaseModel):
    source_id: str
    relation_type: str
    target_id: str
    confidence: float = 1.0
    mirror: bool = True
    confirm_revision: bool = False


class PublicImportRequest(BaseModel):
    catalog: PeopleProfileCatalog
    replace_family_relationships: bool = True
    confirm: bool = False


class PrivateImportRequest(BaseModel):
    payload: IdentityBindingsFile
    replace: bool = False
    confirm: bool = False


def _require_operator(request: Request) -> None:
    if getattr(request.state, "security_role", None) not in {"operator", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operator capability required")


def _refresh_family_authority(core) -> None:
    learner = getattr(getattr(core.cognition, "compiler", None), "relationship_learning", None)
    if learner is not None:
        learner.family_authority_ids = core.profiles.family_authorities()


def _entity_payload(core, entity_id: str) -> dict[str, object]:
    entity = core.identities.get(entity_id)
    return {
        "id": entity_id,
        "name": entity.canonical_name if entity else entity_id,
        "entity_type": entity.entity_type.value if entity else "unknown",
    }


def _binding_ref(binding: PlatformIdentityBinding) -> str:
    material = json.dumps(
        [
            binding.platform.value,
            binding.identifier_type,
            binding.external_actor_id,
            binding.account_scope,
            binding.entity_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _masked_identifier(value: str) -> str:
    if len(value) <= 4:
        return "•" * len(value)
    return "••••" + value[-4:]


def _binding_summary(binding: PlatformIdentityBinding) -> dict[str, object]:
    return {
        "ref": _binding_ref(binding),
        "platform": binding.platform.value,
        "identifier_type": binding.identifier_type,
        "masked_external_actor_id": _masked_identifier(binding.external_actor_id),
        "entity_id": binding.entity_id,
        "account_scope": binding.account_scope,
        "trust": binding.trust.value,
        "confidence": binding.confidence,
        "enabled": binding.enabled,
    }


def _find_binding(core, entity_id: str, ref: str) -> PlatformIdentityBinding | None:
    for binding in core.profiles.bindings_for(entity_id):
        if _binding_ref(binding) == ref:
            return binding
    return None


def _validate_family_relation(core, source_id: str, relation_type: str, target_id: str) -> None:
    if relation_type not in FAMILY_RELATION_TYPES:
        raise HTTPException(status_code=422, detail="unsupported family relation type")
    if source_id == target_id:
        raise HTTPException(status_code=422, detail="relationship endpoints must differ")
    if core.identities.get(source_id) is None or core.identities.get(target_id) is None:
        raise HTTPException(status_code=422, detail="relationship endpoints must be canonical entities")


def _retire_relation(core, source_id: str, relation_type: str, target_id: str) -> bool:
    now = core.clock.now()
    current = core.world_store.current_relationship(source_id, relation_type, target_id, at=now)
    changed = False
    if current is not None:
        changed = core.world_store.expire_relationship(current.id, now) or changed
    changed = core.identities.remove_relationship(source_id, relation_type, target_id) or changed
    # EntityRegistry persistence is a projection table. Remove the retired edge
    # explicitly so a later restart cannot resurrect it before temporal restore.
    with core.database._lock, core.database._conn:
        cursor = core.database._conn.execute(
            "DELETE FROM relationships WHERE source = ? AND relation_type = ? AND target = ?",
            (source_id, relation_type, target_id),
        )
        changed = cursor.rowcount > 0 or changed
    return changed


def _active_family_relationships(core) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    now = core.clock.now()
    for relation in core.identities.relationships:
        if relation.relation_type not in FAMILY_RELATION_TYPES:
            continue
        revision = core.world_store.current_relationship(
            relation.source,
            relation.relation_type,
            relation.target,
            at=now,
        )
        rows.append(
            {
                "source_id": relation.source,
                "source_name": _entity_payload(core, relation.source)["name"],
                "relation_type": relation.relation_type,
                "target_id": relation.target,
                "target_name": _entity_payload(core, relation.target)["name"],
                "confidence": relation.confidence,
                "source_of_truth": revision.source_of_truth if revision else "registry",
                "valid_from": revision.valid_from.isoformat() if revision else None,
            }
        )
    rows.sort(key=lambda item: (str(item["source_name"]), str(item["relation_type"]), str(item["target_name"])))
    return rows


def _catalog_diff(core, catalog: PeopleProfileCatalog, *, replace: bool) -> dict[str, object]:
    current_profiles = {item.entity_id: item for item in core.profiles.profiles()}
    incoming_profiles = {item.entity_id: item for item in catalog.profiles}
    added = sorted(set(incoming_profiles) - set(current_profiles))
    changed = sorted(
        entity_id
        for entity_id in set(incoming_profiles) & set(current_profiles)
        if incoming_profiles[entity_id] != current_profiles[entity_id]
    )
    unchanged = sorted(set(incoming_profiles) & set(current_profiles) - set(changed))

    current_relations = {
        (item.source, item.relation_type, item.target): item.confidence
        for item in core.identities.relationships
        if item.relation_type in FAMILY_RELATION_TYPES
    }
    incoming_relations = {
        (item.source, item.relation_type, item.target): item.confidence
        for item in catalog.relationships
    }
    relation_add = sorted(key for key in incoming_relations if key not in current_relations)
    relation_change = sorted(
        key
        for key in incoming_relations
        if key in current_relations and incoming_relations[key] != current_relations[key]
    )
    managed = set(incoming_profiles) | {"ENTITY_AMORA", "ENTITY_CHATGPT"}
    relation_remove = []
    if replace:
        relation_remove = sorted(
            key
            for key in current_relations
            if key[0] in managed and key[2] in managed and key not in incoming_relations
        )
    return {
        "profile_add": added,
        "profile_change": changed,
        "profile_unchanged": unchanged,
        "relationship_add": [list(key) for key in relation_add],
        "relationship_change": [list(key) for key in relation_change],
        "relationship_remove": [list(key) for key in relation_remove],
    }


def _stage_catalog(core, catalog: PeopleProfileCatalog, *, replace: bool) -> EntityRegistry:
    staged: EntityRegistry = deepcopy(core.identities)
    for profile in catalog.profiles:
        staged.upsert_entity(
            Entity(
                id=profile.entity_id,
                canonical_name=profile.canonical_name,
                aliases=set(profile.aliases),
                entity_type=EntityType.PERSON,
            )
        )
    managed = {profile.entity_id for profile in catalog.profiles} | {"ENTITY_AMORA", "ENTITY_CHATGPT"}
    if replace:
        for relation in tuple(staged.relationships):
            if (
                relation.relation_type in FAMILY_RELATION_TYPES
                and relation.source in managed
                and relation.target in managed
            ):
                staged.remove_relationship(relation.source, relation.relation_type, relation.target)
    for item in catalog.relationships:
        if item.relation_type not in FAMILY_RELATION_TYPES:
            raise ValueError(f"unsupported family relation type: {item.relation_type}")
        staged.upsert_relationship(
            Relationship(
                source=item.source,
                relation_type=item.relation_type,
                target=item.target,
                confidence=item.confidence,
            )
        )
    return staged


def _apply_catalog_atomic(core, catalog: PeopleProfileCatalog, *, replace: bool) -> None:
    """Commit profiles + family registry + temporal revisions in one SQL transaction.

    Semantic validation is performed against a staged EntityRegistry first. The
    SQL transaction then updates all persistent tables using the database's one
    authoritative connection, including relationship_revisions. Only after commit
    is the shared in-memory registry synchronized.
    """
    staged = _stage_catalog(core, catalog, replace=replace)
    now: datetime = core.clock.now()
    now_text = now.isoformat()
    managed = {profile.entity_id for profile in catalog.profiles} | {"ENTITY_AMORA", "ENTITY_CHATGPT"}
    desired = {
        (item.source, item.relation_type, item.target): item.confidence
        for item in catalog.relationships
    }
    source_revision = f"operator:web:{catalog.revision}"
    db = core.database

    with db._lock, db._conn:
        for profile in catalog.profiles:
            db._conn.execute(
                """INSERT INTO entities(id, canonical_name, entity_type, payload_json)
                   VALUES (?, ?, ?, '{}')
                   ON CONFLICT(id) DO UPDATE SET
                     canonical_name=excluded.canonical_name,
                     entity_type=excluded.entity_type""",
                (profile.entity_id, profile.canonical_name, EntityType.PERSON.value),
            )
            db._conn.execute("DELETE FROM aliases WHERE entity_id = ?", (profile.entity_id,))
            for alias in set(profile.aliases) | {profile.canonical_name}:
                db._conn.execute(
                    "INSERT INTO aliases(normalized_alias, entity_id) VALUES (?, ?)",
                    (normalize_alias(alias), profile.entity_id),
                )
            payload = json.dumps(
                profile.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            db._conn.execute(
                """INSERT INTO person_profiles(entity_id, profile_json, source_revision, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(entity_id) DO UPDATE SET
                     profile_json=excluded.profile_json,
                     source_revision=excluded.source_revision,
                     updated_at=excluded.updated_at""",
                (profile.entity_id, payload, source_revision, now_text),
            )

        active_rows = db._conn.execute(
            """SELECT id, source_id, relation_type, target_id, confidence, source_of_truth
               FROM relationship_revisions
               WHERE valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)""",
            (now_text, now_text),
        ).fetchall()
        active = {
            (row["source_id"], row["relation_type"], row["target_id"]): row
            for row in active_rows
            if row["relation_type"] in FAMILY_RELATION_TYPES
        }

        if replace:
            for key, row in active.items():
                if key[0] in managed and key[2] in managed and key not in desired:
                    db._conn.execute(
                        "UPDATE relationship_revisions SET valid_until = ? WHERE id = ?",
                        (now_text, row["id"]),
                    )
                    db._conn.execute(
                        "DELETE FROM relationships WHERE source = ? AND relation_type = ? AND target = ?",
                        key,
                    )

        for key, confidence in desired.items():
            row = active.get(key)
            if row is not None and float(row["confidence"]) != float(confidence):
                db._conn.execute(
                    "UPDATE relationship_revisions SET valid_until = ? WHERE id = ?",
                    (now_text, row["id"]),
                )
                row = None
            if row is None:
                db._conn.execute(
                    """INSERT INTO relationship_revisions(
                           id, source_id, relation_type, target_id, confidence,
                           source_of_truth, valid_from, valid_until, created_at,
                           supersedes_id, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                    (
                        str(uuid4()),
                        key[0],
                        key[1],
                        key[2],
                        confidence,
                        "operator:web:import",
                        now_text,
                        now_text,
                        row["id"] if row is not None else None,
                        json.dumps({"catalog_revision": catalog.revision}, separators=(",", ":")),
                    ),
                )
            db._conn.execute(
                """INSERT INTO relationships(source, relation_type, target, confidence)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source, relation_type, target)
                   DO UPDATE SET confidence=excluded.confidence""",
                (key[0], key[1], key[2], confidence),
            )

    # Update the shared registry in place so every already-wired module sees the
    # same committed state without replacing its object reference.
    for profile in catalog.profiles:
        core.identities.upsert_entity(
            Entity(
                id=profile.entity_id,
                canonical_name=profile.canonical_name,
                aliases=set(profile.aliases),
                entity_type=EntityType.PERSON,
            )
        )
    if replace:
        for relation in tuple(core.identities.relationships):
            if (
                relation.relation_type in FAMILY_RELATION_TYPES
                and relation.source in managed
                and relation.target in managed
                and (relation.source, relation.relation_type, relation.target) not in desired
            ):
                core.identities.remove_relationship(relation.source, relation.relation_type, relation.target)
    for key, confidence in desired.items():
        core.identities.upsert_relationship(
            Relationship(source=key[0], relation_type=key[1], target=key[2], confidence=confidence)
        )
    _refresh_family_authority(core)


def _private_import_diff(core, payload: IdentityBindingsFile, *, replace: bool) -> dict[str, object]:
    incoming_refs = {_binding_ref(item) for item in payload.bindings}
    current = [binding for profile in core.profiles.profiles() for binding in core.profiles.bindings_for(profile.entity_id)]
    current_refs = {_binding_ref(item) for item in current}
    return {
        "incoming_count": len(payload.bindings),
        "add_or_update_count": len(incoming_refs - current_refs),
        "already_present_count": len(incoming_refs & current_refs),
        "remove_count": len(current_refs - incoming_refs) if replace else 0,
    }


def _apply_private_bindings_atomic(core, payload: IdentityBindingsFile, *, replace: bool) -> None:
    for binding in payload.bindings:
        entity = core.identities.get(binding.entity_id)
        if entity is None or entity.entity_type != EntityType.PERSON:
            raise ValueError(f"binding references unknown person: {binding.entity_id}")
    now = core.clock.now().isoformat()
    db = core.database
    with db._lock, db._conn:
        if replace:
            db._conn.execute("DELETE FROM platform_identity_bindings")
        for binding in payload.bindings:
            db._conn.execute(
                """INSERT INTO platform_identity_bindings(
                       platform, identifier_type, external_actor_id, account_scope,
                       entity_id, trust, confidence, enabled, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, identifier_type, account_scope, external_actor_id)
                   DO UPDATE SET
                       entity_id=excluded.entity_id,
                       trust=excluded.trust,
                       confidence=excluded.confidence,
                       enabled=excluded.enabled,
                       updated_at=excluded.updated_at""",
                (
                    binding.platform.value,
                    binding.identifier_type,
                    binding.external_actor_id,
                    binding.account_scope,
                    binding.entity_id,
                    binding.trust.value,
                    binding.confidence,
                    int(binding.enabled),
                    now,
                    now,
                ),
            )


def register_people_admin_routes(app, core, require_auth) -> None:
    protected = [Depends(require_auth), Depends(_require_operator)]

    @app.get("/v1/operator/people", dependencies=protected)
    def operator_people() -> dict[str, object]:
        profiles = []
        for profile in core.profiles.profiles():
            profiles.append(
                {
                    **profile.model_dump(mode="json"),
                    "binding_count": len(core.profiles.bindings_for(profile.entity_id)),
                }
            )
        entities = [
            _entity_payload(core, entity.id)
            for entity in core.identities.entities
            if entity.entity_type in {EntityType.PERSON, EntityType.AI_PERSONA, EntityType.EXTERNAL_AI}
        ]
        return {
            "profiles": profiles,
            "entities": entities,
            "family_relation_types": sorted(FAMILY_RELATION_TYPES),
            "symmetric_relation_types": sorted(SYMMETRIC_RELATION_TYPES),
            "family_authority_ids": sorted(core.profiles.family_authorities()),
        }

    @app.put("/v1/operator/people/{entity_id}", dependencies=protected)
    def operator_put_person(entity_id: str, request: ProfileMutation) -> dict[str, object]:
        if request.profile.entity_id != entity_id:
            raise HTTPException(status_code=422, detail="profile entity_id does not match path")
        current = core.profiles.profile(entity_id)
        authority_changed = (
            current is None and request.profile.can_define_amora_family
        ) or (
            current is not None
            and current.can_define_amora_family != request.profile.can_define_amora_family
        )
        if authority_changed and not request.confirm_family_authority_change:
            raise HTTPException(status_code=409, detail="family authority change requires explicit confirmation")
        try:
            saved = core.profiles.upsert_profile(request.profile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _refresh_family_authority(core)
        return saved.model_dump(mode="json")

    @app.get("/v1/operator/relationships", dependencies=protected)
    def operator_relationships() -> dict[str, object]:
        return {"relationships": _active_family_relationships(core)}

    @app.put("/v1/operator/relationships", dependencies=protected)
    def operator_put_relationship(request: RelationshipMutation) -> dict[str, object]:
        _validate_family_relation(core, request.source_id, request.relation_type, request.target_id)
        current = core.world_store.current_relationship(
            request.source_id,
            request.relation_type,
            request.target_id,
        )
        if current is not None and current.confidence != request.confidence and not request.confirm_revision:
            raise HTTPException(status_code=409, detail="relationship revision requires explicit confirmation")
        relation = core.relationships.record(
            request.source_id,
            request.relation_type,
            request.target_id,
            confidence=request.confidence,
            source_of_truth="operator:web",
            metadata={"operator_edit": True},
        )
        mirrored = None
        if request.mirror and request.relation_type in SYMMETRIC_RELATION_TYPES:
            mirrored = core.relationships.record(
                request.target_id,
                request.relation_type,
                request.source_id,
                confidence=request.confidence,
                source_of_truth="operator:web",
                metadata={"operator_edit": True, "mirrored": True},
            )
        return {
            "relationship": relation.model_dump(mode="json"),
            "mirrored": mirrored.model_dump(mode="json") if mirrored else None,
        }

    @app.delete("/v1/operator/relationships", dependencies=protected)
    def operator_delete_relationship(
        source_id: str = Query(..., min_length=1, max_length=96),
        relation_type: str = Query(..., min_length=1, max_length=80),
        target_id: str = Query(..., min_length=1, max_length=96),
        mirror: bool = Query(default=True),
        confirm: bool = Query(default=False),
    ) -> dict[str, object]:
        if not confirm:
            raise HTTPException(status_code=409, detail="relationship removal requires explicit confirmation")
        _validate_family_relation(core, source_id, relation_type, target_id)
        changed = _retire_relation(core, source_id, relation_type, target_id)
        mirrored = False
        if mirror and relation_type in SYMMETRIC_RELATION_TYPES:
            mirrored = _retire_relation(core, target_id, relation_type, source_id)
        return {"removed": changed, "mirrored_removed": mirrored}

    @app.get("/v1/operator/people/{entity_id}/bindings", dependencies=protected)
    def operator_bindings(entity_id: str) -> dict[str, object]:
        if core.profiles.profile(entity_id) is None:
            raise HTTPException(status_code=404, detail="person profile not found")
        return {"bindings": [_binding_summary(item) for item in core.profiles.bindings_for(entity_id)]}

    @app.get("/v1/operator/people/{entity_id}/bindings/{binding_ref}", dependencies=protected)
    def operator_reveal_binding(entity_id: str, binding_ref: str) -> dict[str, object]:
        item = _find_binding(core, entity_id, binding_ref)
        if item is None:
            raise HTTPException(status_code=404, detail="binding not found")
        return item.model_dump(mode="json")

    @app.post("/v1/operator/people/{entity_id}/bindings", dependencies=protected)
    def operator_add_binding(entity_id: str, binding: PlatformIdentityBinding) -> dict[str, object]:
        if binding.entity_id != entity_id:
            raise HTTPException(status_code=422, detail="binding entity_id does not match path")
        try:
            saved = core.profiles.upsert_binding(binding)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _binding_summary(saved)

    @app.put("/v1/operator/people/{entity_id}/bindings/{binding_ref}", dependencies=protected)
    def operator_edit_binding(entity_id: str, binding_ref: str, binding: PlatformIdentityBinding) -> dict[str, object]:
        if binding.entity_id != entity_id:
            raise HTTPException(status_code=422, detail="binding entity_id does not match path")
        previous = _find_binding(core, entity_id, binding_ref)
        if previous is None:
            raise HTTPException(status_code=404, detail="binding not found")
        if _binding_ref(previous) != _binding_ref(binding):
            disabled = previous.model_copy(update={"enabled": False})
            core.profiles.upsert_binding(disabled)
        saved = core.profiles.upsert_binding(binding)
        return _binding_summary(saved)

    @app.delete("/v1/operator/people/{entity_id}/bindings/{binding_ref}", dependencies=protected)
    def operator_disable_binding(
        entity_id: str,
        binding_ref: str,
        confirm: bool = Query(default=False),
    ) -> dict[str, object]:
        if not confirm:
            raise HTTPException(status_code=409, detail="binding disable requires explicit confirmation")
        previous = _find_binding(core, entity_id, binding_ref)
        if previous is None:
            raise HTTPException(status_code=404, detail="binding not found")
        saved = core.profiles.upsert_binding(previous.model_copy(update={"enabled": False}))
        return _binding_summary(saved)

    @app.get("/v1/operator/people/export", dependencies=protected)
    def operator_people_export() -> dict[str, object]:
        relationships = [
            SeedRelationship(
                source=item.source,
                relation_type=item.relation_type,
                target=item.target,
                confidence=item.confidence,
            )
            for item in core.identities.relationships
            if item.relation_type in FAMILY_RELATION_TYPES
        ]
        catalog = PeopleProfileCatalog(
            schema_version=1,
            revision=core.clock.now().strftime("operator-export-%Y%m%dT%H%M%SZ"),
            profiles=list(core.profiles.profiles()),
            relationships=relationships,
        )
        return catalog.model_dump(mode="json")

    @app.get("/v1/operator/bindings/export", dependencies=protected)
    def operator_bindings_export() -> dict[str, object]:
        payload = IdentityBindingsFile(
            schema_version=1,
            bindings=[
                binding
                for profile in core.profiles.profiles()
                for binding in core.profiles.bindings_for(profile.entity_id)
            ],
        )
        return payload.model_dump(mode="json")

    @app.post("/v1/operator/people/import/preview", dependencies=protected)
    def operator_people_import_preview(request: PublicImportRequest) -> dict[str, object]:
        try:
            _stage_catalog(core, request.catalog, replace=request.replace_family_relationships)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _catalog_diff(core, request.catalog, replace=request.replace_family_relationships)

    @app.post("/v1/operator/people/import/apply", dependencies=protected)
    def operator_people_import_apply(request: PublicImportRequest) -> dict[str, object]:
        if not request.confirm:
            raise HTTPException(status_code=409, detail="people import requires explicit confirmation")
        try:
            _apply_catalog_atomic(core, request.catalog, replace=request.replace_family_relationships)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "applied": True,
            "diff": _catalog_diff(core, request.catalog, replace=request.replace_family_relationships),
        }

    @app.post("/v1/operator/bindings/import/preview", dependencies=protected)
    def operator_bindings_import_preview(request: PrivateImportRequest) -> dict[str, object]:
        for binding in request.payload.bindings:
            entity = core.identities.get(binding.entity_id)
            if entity is None or entity.entity_type != EntityType.PERSON:
                raise HTTPException(status_code=422, detail=f"binding references unknown person: {binding.entity_id}")
        return _private_import_diff(core, request.payload, replace=request.replace)

    @app.post("/v1/operator/bindings/import/apply", dependencies=protected)
    def operator_bindings_import_apply(request: PrivateImportRequest) -> dict[str, object]:
        if not request.confirm:
            raise HTTPException(status_code=409, detail="private bindings import requires explicit confirmation")
        try:
            _apply_private_bindings_atomic(core, request.payload, replace=request.replace)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"applied": True, "binding_count": len(request.payload.bindings)}
