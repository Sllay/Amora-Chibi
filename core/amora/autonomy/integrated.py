from __future__ import annotations

from datetime import timedelta
from typing import Any

from amora.events import AmoraEvent

from .models import DecisionContext, DesireCandidate, DesireKind, DriveState, DriveType, UtilityFactors
from .service import AutonomyEngine as BaseAutonomyEngine, _clamp


class AutonomyEngine(BaseAutonomyEngine):
    """V1 BDI integration for provider action *suggestions*.

    Suggestions are ephemeral evidence, never intentions. They enter the same
    local utility competition as homeostatic drives/social bids and cannot use
    model-reported urgency to trigger the BDI urgent-switch bypass.
    """

    COGNITIVE_SUGGESTION_TTL = timedelta(minutes=10)
    MAX_PENDING_COGNITIVE_SUGGESTIONS = 32
    MAX_MODEL_SUGGESTION_URGENCY = 0.74  # below URGENT_SWITCH_THRESHOLD

    _ACTION_MAP: dict[str, tuple[DesireKind, DriveType, float, float]] = {
        "initiate_contact": (DesireKind.INITIATE_CONTACT, DriveType.SOCIAL, 0.78, 0.10),
        "seek_contact": (DesireKind.INITIATE_CONTACT, DriveType.SOCIAL, 0.76, 0.10),
        "ask_question": (DesireKind.ASK_QUESTION, DriveType.CURIOSITY, 0.72, 0.08),
        "seek_repair": (DesireKind.SEEK_REPAIR, DriveType.SOCIAL, 0.84, 0.14),
        "express_gratitude": (DesireKind.EXPRESS_GRATITUDE, DriveType.EXPRESSION, 0.80, 0.05),
        "set_boundary": (DesireKind.SET_BOUNDARY, DriveType.EXPRESSION, 0.88, 0.12),
        "withdraw": (DesireKind.WITHDRAW, DriveType.EXPRESSION, 0.68, 0.05),
        "wait": (DesireKind.WAIT, DriveType.REST, 0.86, 0.02),
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cognitive_suggestions: dict[str, dict[str, object]] = {}

    async def handle_cognitive_suggestion(self, event: AmoraEvent) -> None:
        if event.event_type != "cognition.action_suggested":
            return
        if event.payload.get("allowed_kind") is not True:
            return
        if event.payload.get("decision") != "deferred_to_bdi":
            return

        action_kind = str(event.payload.get("action_kind") or "")
        if action_kind not in self._ACTION_MAP:
            return
        if event.target is not None and self.identities.get(event.target) is None:
            return

        self._purge_cognitive_suggestions()
        key = str(event.id)
        self._cognitive_suggestions[key] = {
            "event_id": key,
            "action_kind": action_kind,
            "target_id": event.target,
            "confidence": _clamp(event.confidence, 0.0, 1.0),
            "urgency": min(self.MAX_MODEL_SUGGESTION_URGENCY, _clamp(event.importance, 0.0, 1.0)),
            "reason_code": str(event.payload.get("reason_code") or "MODEL_SUGGESTION"),
            "created_at": self.clock.now(),
            "expires_at": self.clock.now() + self.COGNITIVE_SUGGESTION_TTL,
        }
        if len(self._cognitive_suggestions) > self.MAX_PENDING_COGNITIVE_SUGGESTIONS:
            oldest = min(
                self._cognitive_suggestions,
                key=lambda item: self._cognitive_suggestions[item]["created_at"],
            )
            self._cognitive_suggestions.pop(oldest, None)

    def state(self) -> dict[str, object]:
        self._purge_cognitive_suggestions()
        state = super().state()
        state["pending_cognitive_suggestions"] = len(self._cognitive_suggestions)
        return state

    def _collect_candidates(self, drives: list[DriveState], context: DecisionContext) -> list[DesireCandidate]:
        candidates = super()._collect_candidates(drives, context)
        self._purge_cognitive_suggestions()
        drive_map = {item.drive: item for item in drives}
        for suggestion in self._cognitive_suggestions.values():
            candidate = self._candidate_from_cognitive_suggestion(suggestion, drive_map, context)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _candidate_from_cognitive_suggestion(
        self,
        suggestion: dict[str, object],
        drives: dict[DriveType, DriveState],
        context: DecisionContext,
    ) -> DesireCandidate | None:
        action_kind = str(suggestion["action_kind"])
        mapping = self._ACTION_MAP.get(action_kind)
        if mapping is None:
            return None
        kind, primary_drive, base_reward, cost = mapping
        target = suggestion.get("target_id")
        target_id = str(target) if target is not None else None
        confidence = _clamp(float(suggestion.get("confidence") or 0.0), 0.0, 1.0)
        urgency = min(
            self.MAX_MODEL_SUGGESTION_URGENCY,
            _clamp(float(suggestion.get("urgency") or 0.0), 0.0, 1.0),
        )
        canonical_need = drives[primary_drive].need
        factors = UtilityFactors(
            drive_need=canonical_need,
            opportunity=self._target_opportunity(target_id, context) if target_id else context.opportunity,
            personality=0.88,
            # Confidence only modulates an already-local factor. It cannot replace
            # drives, relationship state or interruption gates.
            emotion=_clamp(0.55 + 0.25 * confidence, 0.0, 1.0),
            relationship=self._relationship_factor(target_id),
            expected_reward=_clamp(base_reward * (0.85 + 0.15 * confidence), 0.0, 1.0),
            cost=cost,
            interruption_penalty=self._interruption_penalty(context, target_id),
        )
        return DesireCandidate(
            kind=kind,
            source_kind="cognitive_suggestion",
            source_ref_id=str(suggestion["event_id"]),
            target_id=target_id,
            primary_drive=primary_drive,
            factors=factors,
            utility=self._utility(factors),
            urgency=urgency,
            created_at=suggestion["created_at"],
            expires_at=suggestion["expires_at"],
            metadata={
                "suggested_action_kind": action_kind,
                "provider_confidence": confidence,
                "provider_reason_code": suggestion.get("reason_code"),
                "model_cannot_execute_directly": True,
                "utility_recomputed_locally": True,
            },
        )

    def _intention_from_candidate(self, candidate: DesireCandidate):
        intention = super()._intention_from_candidate(candidate)
        if candidate.source_kind == "cognitive_suggestion" and candidate.source_ref_id:
            self._cognitive_suggestions.pop(candidate.source_ref_id, None)
        return intention

    def _purge_cognitive_suggestions(self) -> None:
        now = self.clock.now()
        expired = [
            key
            for key, item in self._cognitive_suggestions.items()
            if item["expires_at"] <= now
        ]
        for key in expired:
            self._cognitive_suggestions.pop(key, None)
