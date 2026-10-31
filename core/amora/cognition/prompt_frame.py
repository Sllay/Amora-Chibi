from __future__ import annotations

import json
from typing import Any

from .models import CognitiveContext


PROVIDER_FRAME_PROTOCOL = "amora.provider_frame.v1"


def provider_frame(context: CognitiveContext) -> dict[str, object]:
    """Build the provider-facing semantic frame without duplicating literal history.

    The frame tells a replaceable model what the Core has already decided and,
    importantly, keeps participant identity independent from provider-native chat
    roles. A provider may receive many ``role=user`` messages that came from
    different people; ``speaker_id``/``addressee_id`` remain the authoritative
    social identity for every turn.
    """

    recent_routes: list[dict[str, object]] = []
    participants: set[str] = set()

    _add_entity(participants, context.speaker_id)
    _add_entity(participants, context.target_id)

    for item in context.recent_messages:
        speaker_id = _clean_id(item.get("speaker_id"))
        addressee_id = _clean_id(item.get("addressee_id"))
        _add_entity(participants, speaker_id)
        _add_entity(participants, addressee_id)
        recent_routes.append(
            {
                "turn_id": str(item.get("turn_id") or ""),
                "speaker_id": speaker_id,
                "addressee_id": addressee_id,
                "source": str(item.get("source") or ""),
                "status": str(item.get("status") or ""),
            }
        )

    for relation in context.relationships:
        _add_entity(participants, _clean_id(relation.get("source")))
        _add_entity(participants, _clean_id(relation.get("target")))

    acir = context.acir if isinstance(context.acir, dict) else None
    if acir:
        _add_entity(participants, _clean_id(acir.get("speaker")))
        _add_entity(participants, _clean_id(acir.get("addressee")))
        for entity_id in acir.get("entities") or []:
            _add_entity(participants, _clean_id(entity_id))

    canonical_state: dict[str, object] = {
        "self_model": context.self_model,
        "relationships": context.relationships,
        "mood": context.mood,
        "directed_affect": context.directed_affect,
        "drives": context.drives,
        "active_intention": context.active_intention,
        "memories": [item.model_dump(mode="json") for item in context.memories],
        "world_claims": context.world_claims,
        "reason_codes": context.reason_codes,
    }
    if context.specialist_support:
        canonical_state["specialist_support"] = context.specialist_support

    return {
        "protocol": PROVIDER_FRAME_PROTOCOL,
        "authority": {
            "canonical_state_owner": "AMORA_CORE",
            "dialogue_plan_owner": "AMORA_CORE",
            "provider_role": "SURFACE_REALIZER",
            "participant_identity_rule": (
                "speaker_id/addressee_id identify people and entities; provider chat roles do not"
            ),
            "multi_user_rule": (
                "different role=user messages may belong to different speakers; never merge them"
            ),
        },
        "current_turn": {
            "turn_id": str(context.turn_id),
            "speaker_id": context.speaker_id,
            "addressee_id": context.target_id,
            "acir": acir,
        },
        "social_scene": {
            "participant_ids": sorted(participants),
            "recent_turn_routes": recent_routes,
        },
        "canonical_state": canonical_state,
        "dialogue_plan": context.dialogue_plan,
        "privacy": {
            "scope": context.privacy_scope.value,
            "remote_processing_allowed": context.remote_processing_allowed,
        },
    }


def render_provider_frame(context: CognitiveContext) -> str:
    return (
        f"AMORA PROVIDER FRAME — {PROVIDER_FRAME_PROTOCOL}\n"
        + json.dumps(provider_frame(context), ensure_ascii=False, separators=(",", ":"))
    )


def render_conversation_turn(
    *,
    turn_id: object,
    speaker_id: object,
    addressee_id: object,
    text: str,
    source: object = None,
) -> str:
    """Serialize one literal turn with identity outside the free-form text.

    JSON escaping prevents user text that happens to contain labels such as
    ``speaker_id`` from becoming indistinguishable from the trusted envelope.
    """

    payload = {
        "turn_id": str(turn_id or ""),
        "speaker_id": _clean_id(speaker_id),
        "addressee_id": _clean_id(addressee_id),
        "source": str(source or ""),
        "text": text,
    }
    return "AMORA CONVERSATION TURN\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def render_current_turn(context: CognitiveContext) -> str:
    return render_conversation_turn(
        turn_id=context.turn_id,
        speaker_id=context.speaker_id,
        addressee_id=context.target_id,
        text=context.user_text,
        source="current_turn",
    )


def _clean_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _add_entity(target: set[str], value: object) -> None:
    entity_id = _clean_id(value)
    if entity_id:
        target.add(entity_id)
