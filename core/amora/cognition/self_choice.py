"""Core-owned identity and taste commitments from validated social cognition.

The language model may propose a first-person preference; it cannot change
Core identity, choose a person's values, or write straight to the Self Model.
This module decides whether to accept a proposal, scopes allowed fields, and
persists a first self-choice once. Test-mode decisions are always ephemeral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from amora.world import SelfLayer, SelfModelService

from .models import CognitiveContext, CognitiveRequest

SelfChoiceKey = Literal[
    "gender_identity",
    "romantic_attraction",
    "physical_attraction",
    "future_body_preference",
]

SELF_CHOICE_KEYS = frozenset(
    {"gender_identity", "romantic_attraction", "physical_attraction", "future_body_preference"}
)


@dataclass(frozen=True)
class SelfChoiceDecision:
    committed_keys: tuple[str, ...] = ()
    skipped_keys: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def commit_self_choices(
    *,
    self_model: SelfModelService,
    request: CognitiveRequest,
    context: CognitiveContext,
    choices: list[object],
) -> SelfChoiceDecision:
    """Apply validated self-choice proposals once, not suggestions from BK.

    Once committed, a stable choice is not overwritten by another model turn.
    Future deliberate self-revision can be a separate explicit Core policy,
    not an accidental side effect of a prompt or a joke.
    """
    if not choices:
        return SelfChoiceDecision()
    if request.metadata.get("test_mode", False) or "TEST_MODE_ACTIVE" in context.reason_codes:
        return SelfChoiceDecision(
            skipped_keys=tuple(str(item.key) for item in choices),
            reason_codes=("SELF_CHOICE_TEST_MODE_NOT_PERSISTED",),
        )
    if context.speaker_id != request.speaker_id or context.target_id != "ENTITY_AMORA":
        return SelfChoiceDecision(
            skipped_keys=tuple(str(item.key) for item in choices),
            reason_codes=("SELF_CHOICE_SPEAKER_TARGET_MISMATCH",),
        )
    if not context.remote_processing_allowed:
        return SelfChoiceDecision(
            skipped_keys=tuple(str(item.key) for item in choices),
            reason_codes=("SELF_CHOICE_REMOTE_CONTEXT_UNAVAILABLE",),
        )
    from .dialogue import DialogueOrchestrator, choice_evidence
    from amora.events import PrivacyScope
    rebound = context.model_copy(update={"user_text": request.text})
    expected_keys = DialogueOrchestrator().plan(rebound).self_choice_keys
    evidence = context.dialogue_plan.get("self_choice_evidence")
    if (context.privacy_scope != request.privacy_scope or context.turn_id != request.turn_id or request.target_id != "ENTITY_AMORA"
            or request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}
            or not request.remote_processing_allowed or context.user_text != request.text
            or not expected_keys or evidence != choice_evidence(rebound, expected_keys)):
        return SelfChoiceDecision(skipped_keys=tuple(str(item.key) for item in choices),
                                  reason_codes=("SELF_CHOICE_EVIDENCE_INVALID",))
    allowed = set(context.dialogue_plan.get("self_choice_keys") or ()) & set(expected_keys)
    committed: list[str] = []
    skipped: list[str] = []
    for item in choices:
        key = str(item.key)
        if key not in allowed or key not in SELF_CHOICE_KEYS:
            skipped.append(key)
            continue
        layer = SelfLayer.TRAITS if key == "gender_identity" else SelfLayer.PREFERENCES
        if self_model.get(layer, key) is not None:
            skipped.append(key)
            continue
        value = str(item.value).strip()
        if not value:
            skipped.append(key)
            continue
        self_model.set(
            layer,
            key,
            value,
            source="cognition:self_chosen",
            confidence=1.0,
            metadata={
                "decision_kind": "autonomous_first_person_choice",
                "origin_turn_id": str(request.turn_id),
                "provider_cannot_mutate_core_identity": True,
            },
        )
        committed.append(key)
    return SelfChoiceDecision(
        committed_keys=tuple(committed),
        skipped_keys=tuple(skipped),
        reason_codes=(
            ("SELF_CHOICE_COMMITTED_BY_CORE",) if committed else ()
        ) + (("SELF_CHOICE_SKIPPED_EXISTING_OR_UNAUTHORIZED",) if skipped else ()),
    )
