from __future__ import annotations

from datetime import timedelta
from math import pow

from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.identity import EntityRegistry
from amora.time import Clock

from .models import (
    AppraisalInput,
    AppraisalResult,
    EmotionEvent,
    EmotionProcessResult,
    EmotionType,
    EntityAffect,
    MoodState,
    SocialBid,
    SocialBidStatus,
    SocialBidType,
)
from .store import EmotionStore


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _decay_to(value: float, baseline: float, elapsed_seconds: float, half_life_seconds: float) -> float:
    if elapsed_seconds <= 0:
        return value
    factor = pow(0.5, elapsed_seconds / half_life_seconds)
    return baseline + (value - baseline) * factor


class AppraisalEngine:
    """Deterministic appraisal: emotion emerges from dimensions, not random prompting."""

    MIN_EVENT_INTENSITY = 0.08

    _MOOD_VECTORS: dict[EmotionType, tuple[float, float, float]] = {
        EmotionType.JOY: (0.72, 0.28, 0.20),
        EmotionType.GRATITUDE: (0.60, 0.18, 0.08),
        EmotionType.RELIEF: (0.52, -0.18, 0.22),
        EmotionType.ANGER: (-0.70, 0.62, 0.28),
        EmotionType.IRRITATION: (-0.48, 0.38, 0.08),
        EmotionType.FEAR: (-0.72, 0.70, -0.55),
        EmotionType.DISAPPOINTMENT: (-0.56, 0.10, -0.24),
    }

    def appraise(self, item: AppraisalInput) -> AppraisalResult:
        surprise = 1.0 - item.expectedness
        significance = (
            0.24 * item.goal_relevance
            + 0.16 * item.novelty
            + 0.08 * surprise
            + 0.10 * item.agency
            + 0.08 * (1.0 - item.control)
            + 0.14 * item.intentionality
            + 0.12 * item.norm_violation
            + 0.08 * item.relationship_relevance
        )
        intensity = _clamp(abs(item.outcome_valence) * 0.60 + significance * 0.40, 0.0, 1.0)
        spillover = _clamp(
            0.10
            + 0.30 * item.goal_relevance
            + 0.20 * item.novelty
            + 0.18 * item.relationship_relevance
            + 0.12 * surprise,
            0.0,
            1.0,
        )
        blame = 0.0
        reasons = ["APPRAISAL_DETERMINISTIC"]

        emotion: EmotionType | None
        if abs(item.outcome_valence) < 0.08 and intensity < 0.25:
            emotion = None
            reasons.append("BELOW_EMOTION_THRESHOLD")
        elif item.outcome_valence < 0:
            blame = _clamp(item.agency * item.intentionality * (0.4 + 0.6 * item.norm_violation), 0.0, 1.0)
            if item.control <= 0.28 and (item.novelty >= 0.55 or item.expectedness <= 0.35):
                emotion = EmotionType.FEAR
                reasons.append("LOW_CONTROL_THREAT")
            elif item.agent_id and item.agency >= 0.45 and (item.intentionality >= 0.55 or item.norm_violation >= 0.55):
                emotion = EmotionType.ANGER
                reasons.append("AGENT_BLAME_ATTRIBUTION")
            elif item.goal_relevance >= 0.65 and item.expectedness >= 0.55:
                emotion = EmotionType.DISAPPOINTMENT
                reasons.append("GOAL_EXPECTATION_VIOLATED")
            else:
                emotion = EmotionType.IRRITATION
                reasons.append("NEGATIVE_LOW_BLAME")
        else:
            if item.agent_id and item.agency >= 0.40 and item.relationship_relevance >= 0.35 and item.intentionality >= 0.35:
                emotion = EmotionType.GRATITUDE
                reasons.append("BENEFICIAL_AGENT_ATTRIBUTION")
            elif item.control <= 0.35 and item.expectedness <= 0.40:
                emotion = EmotionType.RELIEF
                reasons.append("POSITIVE_UNEXPECTED_RELEASE")
            else:
                emotion = EmotionType.JOY
                reasons.append("POSITIVE_OUTCOME")

        if emotion is None:
            return AppraisalResult(
                emotion=None,
                intensity=intensity,
                blame=blame,
                spillover=spillover,
                mood_valence_delta=0.0,
                mood_arousal_delta=0.0,
                mood_dominance_delta=0.0,
                reason_codes=reasons,
            )

        vector = self._MOOD_VECTORS[emotion]
        scale = intensity * spillover
        return AppraisalResult(
            emotion=emotion,
            intensity=intensity,
            blame=blame,
            spillover=spillover,
            mood_valence_delta=_clamp(vector[0] * scale, -1.0, 1.0),
            mood_arousal_delta=_clamp(vector[1] * scale, -1.0, 1.0),
            mood_dominance_delta=_clamp(vector[2] * scale, -1.0, 1.0),
            reason_codes=reasons,
        )


class EmotionEngine:
    MOOD_HALF_LIVES = {"valence": 3600.0, "arousal": 1800.0, "dominance": 5400.0}
    AFFECT_HALF_LIVES = {
        "anger": 10800.0,
        "affection": 604800.0,
        "trust": 1209600.0,
        "irritation": 5400.0,
        "fear": 10800.0,
        "gratitude": 28800.0,
    }

    def __init__(
        self,
        store: EmotionStore,
        clock: Clock,
        identities: EntityRegistry,
        events: AsyncEventBus,
        appraisal: AppraisalEngine | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.identities = identities
        self.events = events
        self.appraisal = appraisal or AppraisalEngine()

    def close(self) -> None:
        self.store.close()

    def current_mood(self) -> MoodState:
        mood = self.store.get_mood()
        now = self.clock.now()
        elapsed = max(0.0, (now - mood.updated_at).total_seconds())
        decayed = MoodState(
            valence=_clamp(_decay_to(mood.valence, 0.0, elapsed, self.MOOD_HALF_LIVES["valence"]), -1.0, 1.0),
            arousal=_clamp(_decay_to(mood.arousal, 0.0, elapsed, self.MOOD_HALF_LIVES["arousal"]), 0.0, 1.0),
            dominance=_clamp(_decay_to(mood.dominance, 0.0, elapsed, self.MOOD_HALF_LIVES["dominance"]), -1.0, 1.0),
            updated_at=now,
        )
        self.store.put_mood(decayed)
        return decayed

    def affect_for(self, entity_id: str) -> EntityAffect:
        if self.identities.get(entity_id) is None:
            raise ValueError(f"unknown canonical entity: {entity_id}")
        current = self.store.get_affect(entity_id) or EntityAffect(entity_id=entity_id, updated_at=self.clock.now())
        now = self.clock.now()
        elapsed = max(0.0, (now - current.updated_at).total_seconds())
        decayed = EntityAffect(
            entity_id=entity_id,
            anger=_clamp(_decay_to(current.anger, 0.0, elapsed, self.AFFECT_HALF_LIVES["anger"]), 0.0, 1.0),
            affection=_clamp(_decay_to(current.affection, 0.0, elapsed, self.AFFECT_HALF_LIVES["affection"]), 0.0, 1.0),
            trust=_clamp(_decay_to(current.trust, 0.5, elapsed, self.AFFECT_HALF_LIVES["trust"]), 0.0, 1.0),
            irritation=_clamp(_decay_to(current.irritation, 0.0, elapsed, self.AFFECT_HALF_LIVES["irritation"]), 0.0, 1.0),
            fear=_clamp(_decay_to(current.fear, 0.0, elapsed, self.AFFECT_HALF_LIVES["fear"]), 0.0, 1.0),
            gratitude=_clamp(_decay_to(current.gratitude, 0.0, elapsed, self.AFFECT_HALF_LIVES["gratitude"]), 0.0, 1.0),
            updated_at=now,
        )
        self.store.put_affect(decayed)
        return decayed

    async def process(self, item: AppraisalInput) -> EmotionProcessResult:
        if item.privacy_scope == PrivacyScope.AUTH:
            raise ValueError("AUTH content cannot enter the Emotion Engine")
        for entity_id in {item.target_id, item.agent_id} - {None}:
            if self.identities.get(entity_id) is None:
                raise ValueError(f"unknown canonical entity: {entity_id}")

        if item.dedupe_key:
            existing = self.store.get_event_by_dedupe(item.dedupe_key)
            if existing is not None:
                appraisal = self.appraisal.appraise(existing.appraisal)
                return EmotionProcessResult(
                    appraisal=appraisal,
                    event=existing,
                    mood=self.current_mood(),
                    target_affect=self.affect_for(existing.target_id) if existing.target_id else None,
                    social_bids=[],
                    deduplicated=True,
                )

        result = self.appraisal.appraise(item)
        mood = self.current_mood()
        if result.emotion is None or result.intensity < self.appraisal.MIN_EVENT_INTENSITY:
            return EmotionProcessResult(appraisal=result, mood=mood)

        now = self.clock.now()
        event = EmotionEvent(
            emotion=result.emotion,
            intensity=result.intensity,
            cause_event_id=item.cause_event_id,
            cause_description=item.description,
            target_id=item.target_id,
            agent_id=item.agent_id,
            blame=result.blame,
            intentionality=item.intentionality,
            spillover=result.spillover,
            appraisal=item,
            privacy_scope=item.privacy_scope,
            created_at=now,
            dedupe_key=item.dedupe_key,
        )
        if not self.store.put_event(event):
            existing = self.store.get_event_by_dedupe(item.dedupe_key) if item.dedupe_key else None
            if existing is not None:
                return EmotionProcessResult(
                    appraisal=result,
                    event=existing,
                    mood=self.current_mood(),
                    target_affect=self.affect_for(existing.target_id) if existing.target_id else None,
                    social_bids=[],
                    deduplicated=True,
                )
            raise RuntimeError("emotion event persistence failed")

        mood = MoodState(
            valence=_clamp(mood.valence + result.mood_valence_delta, -1.0, 1.0),
            arousal=_clamp(mood.arousal + result.mood_arousal_delta, 0.0, 1.0),
            dominance=_clamp(mood.dominance + result.mood_dominance_delta, -1.0, 1.0),
            updated_at=now,
        )
        self.store.put_mood(mood)

        target_affect = self._apply_target_affect(event) if event.target_id else None
        bids = self._generate_social_bids(event)
        for bid in bids:
            self.store.put_bid(bid)

        await self.events.publish(
            AmoraEvent(
                event_type="emotion.generated",
                source="emotion_engine",
                actor="ENTITY_AMORA",
                target=event.target_id,
                confidence=1.0,
                importance=event.intensity,
                payload={
                    "emotion_event_id": str(event.id),
                    "emotion": event.emotion.value,
                    "intensity": event.intensity,
                    "blame": event.blame,
                    "spillover": event.spillover,
                },
                privacy_scope=event.privacy_scope,
                causation_id=event.cause_event_id,
                causal_parent=event.cause_event_id,
                dedupe_key=f"emotion.generated:{event.id}",
            )
        )
        return EmotionProcessResult(
            appraisal=result,
            event=event,
            mood=mood,
            target_affect=target_affect,
            social_bids=bids,
        )

    async def process_event(self, event: AmoraEvent, **appraisal_dimensions: float) -> EmotionProcessResult:
        return await self.process(
            AppraisalInput(
                description=f"event:{event.event_type}",
                outcome_valence=float(appraisal_dimensions.pop("outcome_valence")),
                target_id=event.actor or event.target,
                agent_id=event.actor,
                cause_event_id=event.id,
                privacy_scope=event.privacy_scope,
                dedupe_key=f"appraisal:{event.id}",
                **appraisal_dimensions,
            )
        )

    def pending_bids(self, limit: int = 50) -> list[SocialBid]:
        return self.store.list_bids(limit=limit)

    def consume_bid(self, bid_id: str) -> bool:
        return self.store.set_bid_status(bid_id, SocialBidStatus.CONSUMED)

    def _apply_target_affect(self, event: EmotionEvent) -> EntityAffect:
        assert event.target_id is not None
        affect = self.affect_for(event.target_id)
        intensity = event.intensity
        rel = event.appraisal.relationship_relevance
        if event.emotion == EmotionType.ANGER:
            affect.anger = _clamp(affect.anger + intensity * (0.55 + 0.35 * event.blame), 0.0, 1.0)
            affect.irritation = _clamp(affect.irritation + intensity * 0.25, 0.0, 1.0)
            affect.trust = _clamp(affect.trust - intensity * event.blame * 0.08, 0.0, 1.0)
            affect.affection = _clamp(affect.affection - intensity * rel * 0.025, 0.0, 1.0)
        elif event.emotion == EmotionType.IRRITATION:
            affect.irritation = _clamp(affect.irritation + intensity * 0.70, 0.0, 1.0)
            affect.anger = _clamp(affect.anger + intensity * event.blame * 0.18, 0.0, 1.0)
        elif event.emotion == EmotionType.FEAR:
            affect.fear = _clamp(affect.fear + intensity * 0.75, 0.0, 1.0)
            affect.trust = _clamp(affect.trust - intensity * event.blame * 0.05, 0.0, 1.0)
        elif event.emotion == EmotionType.GRATITUDE:
            affect.gratitude = _clamp(affect.gratitude + intensity * 0.75, 0.0, 1.0)
            affect.affection = _clamp(affect.affection + intensity * rel * 0.08, 0.0, 1.0)
            affect.trust = _clamp(affect.trust + intensity * event.appraisal.agency * 0.06, 0.0, 1.0)
        elif event.emotion == EmotionType.JOY:
            affect.affection = _clamp(affect.affection + intensity * rel * 0.035, 0.0, 1.0)
        elif event.emotion == EmotionType.DISAPPOINTMENT:
            affect.irritation = _clamp(affect.irritation + intensity * 0.20, 0.0, 1.0)
            affect.trust = _clamp(affect.trust - intensity * event.blame * 0.035, 0.0, 1.0)
        affect.updated_at = self.clock.now()
        self.store.put_affect(affect)
        return affect

    def _generate_social_bids(self, event: EmotionEvent) -> list[SocialBid]:
        target = event.target_id
        if target is None:
            return []
        now = self.clock.now()
        rel = event.appraisal.relationship_relevance
        spec: tuple[SocialBidType, float, str, timedelta] | None = None
        if event.emotion == EmotionType.GRATITUDE and event.intensity >= 0.20:
            spec = (SocialBidType.EXPRESS_GRATITUDE, event.intensity, "gratitude_toward_target", timedelta(minutes=30))
        elif event.emotion == EmotionType.JOY and rel >= 0.60 and event.intensity >= 0.35:
            spec = (SocialBidType.SEEK_CONTACT, event.intensity * rel, "positive_relationship_activation", timedelta(minutes=20))
        elif event.emotion in {EmotionType.ANGER, EmotionType.IRRITATION}:
            if event.intensity >= 0.60 or event.blame >= 0.55:
                spec = (SocialBidType.SET_BOUNDARY, max(event.intensity, event.blame), "directed_negative_affect", timedelta(minutes=45))
            elif rel >= 0.55:
                spec = (SocialBidType.SEEK_REPAIR, event.intensity * rel, "relationship_repair_candidate", timedelta(hours=1))
            else:
                spec = (SocialBidType.WITHDRAW, event.intensity, "low_intensity_negative_distance", timedelta(minutes=30))
        elif event.emotion == EmotionType.FEAR:
            spec = (SocialBidType.WITHDRAW, event.intensity, "fear_distance_candidate", timedelta(minutes=30))
        elif event.emotion == EmotionType.DISAPPOINTMENT and rel >= 0.55:
            spec = (SocialBidType.SEEK_REPAIR, event.intensity * rel, "disappointment_repair_candidate", timedelta(hours=1))
        if spec is None:
            return []
        bid_type, strength, reason, ttl = spec
        return [
            SocialBid(
                bid_type=bid_type,
                target_id=target,
                strength=_clamp(strength, 0.0, 1.0),
                reason=reason,
                source_emotion_event_id=event.id,
                created_at=now,
                expires_at=now + ttl,
                metadata={"candidate_only": True, "requires_v0_6_decision": True},
            )
        ]
