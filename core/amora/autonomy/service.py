from __future__ import annotations

from datetime import timedelta

from amora.emotion import EmotionEngine, SocialBid, SocialBidType
from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.identity import EntityRegistry
from amora.time import Clock, Scheduler
from amora.world import WorldModelService

from .models import (
    AutonomyCycleResult,
    BeliefSnapshot,
    DecisionContext,
    DecisionOutcome,
    DecisionRecord,
    DesireCandidate,
    DesireKind,
    DriveAdjustment,
    DriveState,
    DriveType,
    Intention,
    IntentionStatus,
    UtilityFactors,
)
from .store import AutonomyStore


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class AutonomyEngine:
    """V0.6 decision layer: drives -> desires -> utility -> BDI intention.

    This layer deliberately stops before planning/execution. V0.7 owns tools,
    plans and action execution.
    """

    MIN_ACTION_UTILITY = 0.18
    HYSTERESIS_MARGIN = 0.12
    URGENT_SWITCH_THRESHOLD = 0.88
    DEFAULT_INTENTION_TTL = timedelta(minutes=30)

    DEFAULT_DRIVES: dict[DriveType, tuple[float, float, float]] = {
        DriveType.SOCIAL: (0.35, 0.25, 0.035),
        DriveType.CURIOSITY: (0.30, 0.20, 0.025),
        DriveType.PLAY: (0.22, 0.15, 0.018),
        DriveType.NOVELTY: (0.20, 0.15, 0.015),
        DriveType.EXPRESSION: (0.25, 0.18, 0.025),
        DriveType.COMPETENCE: (0.20, 0.15, 0.012),
        DriveType.REST: (0.12, 0.10, 0.020),
    }

    def __init__(
        self,
        store: AutonomyStore,
        clock: Clock,
        identities: EntityRegistry,
        emotion: EmotionEngine,
        world: WorldModelService,
        events: AsyncEventBus,
    ) -> None:
        self.store = store
        self.clock = clock
        self.identities = identities
        self.emotion = emotion
        self.world = world
        self.events = events
        self._ensure_default_drives()

    def close(self) -> None:
        self.store.close()

    def _ensure_default_drives(self) -> None:
        now = self.clock.now()
        for drive, (need, baseline, growth) in self.DEFAULT_DRIVES.items():
            self.store.ensure_drive(
                DriveState(
                    drive=drive,
                    need=need,
                    baseline=baseline,
                    growth_per_hour=growth,
                    updated_at=now,
                )
            )

    def current_drives(self) -> list[DriveState]:
        now = self.clock.now()
        states: list[DriveState] = []
        for current in self.store.list_drives():
            elapsed_hours = max(0.0, (now - current.updated_at).total_seconds() / 3600.0)
            need = _clamp(current.need + current.growth_per_hour * elapsed_hours, 0.0, 1.0)
            updated = DriveState(
                drive=current.drive,
                need=need,
                baseline=current.baseline,
                growth_per_hour=current.growth_per_hour,
                updated_at=now,
            )
            self.store.put_drive(updated)
            states.append(updated)
        return sorted(states, key=lambda item: item.drive.value)

    def adjust_drive(
        self,
        drive: DriveType,
        delta: float,
        *,
        reason: str,
        source_event_id=None,
        dedupe_key: str | None = None,
    ) -> tuple[DriveState, bool]:
        if dedupe_key:
            existing = self.store.get_adjustment_by_dedupe(dedupe_key)
            if existing is not None:
                current = {item.drive: item for item in self.current_drives()}[drive]
                return current, True
        states = {item.drive: item for item in self.current_drives()}
        current = states[drive]
        adjustment = DriveAdjustment(
            drive=drive,
            delta=_clamp(delta, -1.0, 1.0),
            reason=reason,
            source_event_id=source_event_id,
            created_at=self.clock.now(),
            dedupe_key=dedupe_key,
        )
        if not self.store.put_adjustment(adjustment):
            current = {item.drive: item for item in self.current_drives()}[drive]
            return current, True
        updated = current.model_copy(
            update={
                "need": _clamp(current.need + adjustment.delta, 0.0, 1.0),
                "updated_at": self.clock.now(),
            }
        )
        self.store.put_drive(updated)
        return updated, False

    def state(self) -> dict[str, object]:
        return {
            "drives": [item.model_dump(mode="json") for item in self.current_drives()],
            "current_intention": (
                self.store.current_intention().model_dump(mode="json")
                if self.store.current_intention()
                else None
            ),
            "decision_count": self.store.decision_count(),
        }

    async def cycle(
        self,
        context: DecisionContext | None = None,
        *,
        dedupe_key: str | None = None,
    ) -> AutonomyCycleResult:
        if dedupe_key:
            existing = self.store.get_cycle_by_dedupe(dedupe_key)
            if existing is not None:
                return existing

        active_context = context or DecisionContext()
        drives = self.current_drives()
        beliefs = self._belief_snapshot(active_context)
        candidates = self._collect_candidates(drives, active_context)
        candidates.sort(key=lambda item: (item.utility, item.urgency), reverse=True)
        silence_utility = self._silence_utility(drives, active_context)
        current = self.store.current_intention()
        previous_id = current.id if current else None
        now = self.clock.now()
        reasons: list[str] = []
        selected: Intention | None = None
        outcome: DecisionOutcome
        selected_utility: float | None = None

        best = candidates[0] if candidates else None

        if current is not None and not active_context.can_interrupt:
            selected = current
            outcome = DecisionOutcome.DEFERRED
            selected_utility = current.selected_utility
            reasons.extend(["CURRENT_INTENTION_PRESERVED", "CONTEXT_CANNOT_INTERRUPT"])
        elif current is not None and best is not None and self._same_source(current, best):
            current.selected_utility = best.utility
            current.urgency = best.urgency
            current.updated_at = now
            self.store.put_intention(current)
            selected = current
            outcome = DecisionOutcome.KEPT_CURRENT
            selected_utility = best.utility
            reasons.extend(["CURRENT_INTENTION_MATCHES_BEST", "HYSTERESIS_KEEP"])
        elif current is not None and best is not None:
            switch_threshold = current.selected_utility + self.HYSTERESIS_MARGIN * (0.5 + current.commitment_strength / 2.0)
            urgent_switch = best.urgency >= self.URGENT_SWITCH_THRESHOLD and best.urgency > current.urgency
            if not urgent_switch and best.utility < switch_threshold:
                selected = current
                outcome = DecisionOutcome.KEPT_CURRENT
                selected_utility = current.selected_utility
                reasons.extend(["CURRENT_INTENTION_PRESERVED", "HYSTERESIS_BLOCKED_SWITCH"])
            else:
                self.store.set_intention_status(current.id, IntentionStatus.CANCELLED)
                selected = self._intention_from_candidate(best)
                self.store.put_intention(selected)
                outcome = DecisionOutcome.SELECTED
                selected_utility = best.utility
                reasons.append("URGENT_SWITCH" if urgent_switch else "UTILITY_SWITCH")
        elif current is not None and best is None:
            selected = current
            outcome = DecisionOutcome.KEPT_CURRENT
            selected_utility = current.selected_utility
            reasons.extend(["NO_NEW_DESIRE", "CURRENT_INTENTION_PRESERVED"])
        elif best is None or best.utility < self.MIN_ACTION_UTILITY or silence_utility >= best.utility:
            outcome = DecisionOutcome.SILENCE
            reasons.append("SILENCE_SELECTED")
            if best is None:
                reasons.append("NO_ACTION_CANDIDATE")
            elif best.utility < self.MIN_ACTION_UTILITY:
                reasons.append("UTILITY_BELOW_ACTION_THRESHOLD")
            else:
                reasons.append("SILENCE_UTILITY_WON")
        else:
            selected = self._intention_from_candidate(best)
            self.store.put_intention(selected)
            outcome = DecisionOutcome.SELECTED
            selected_utility = best.utility
            reasons.extend(["BEST_UTILITY_SELECTED", "BDI_INTENTION_COMMITTED"])

        decision = DecisionRecord(
            outcome=outcome,
            selected_intention_id=selected.id if selected else None,
            previous_intention_id=previous_id,
            selected_utility=selected_utility,
            silence_utility=silence_utility,
            reason_codes=reasons,
            created_at=now,
            dedupe_key=dedupe_key,
        )
        result = AutonomyCycleResult(
            beliefs=beliefs,
            drives=drives,
            candidates=candidates,
            decision=decision,
            intention=selected,
        )
        if not self.store.put_cycle_result(result):
            if dedupe_key:
                duplicate = self.store.get_cycle_by_dedupe(dedupe_key)
                if duplicate is not None:
                    return duplicate
            raise RuntimeError("autonomy decision persistence failed")

        await self.events.publish(
            AmoraEvent(
                event_type="autonomy.decision",
                source="autonomy_engine",
                actor="ENTITY_AMORA",
                target=selected.target_id if selected else None,
                confidence=1.0,
                importance=max(0.2, selected.urgency if selected else silence_utility),
                payload={
                    "decision_id": str(decision.id),
                    "outcome": decision.outcome.value,
                    "intention_id": str(selected.id) if selected else None,
                    "intention_kind": selected.kind.value if selected else None,
                    "selected_utility": selected_utility,
                    "silence_utility": silence_utility,
                    "reason_codes": reasons,
                },
                privacy_scope=PrivacyScope.PERSONAL,
                dedupe_key=f"autonomy.decision:{decision.id}",
            )
        )
        return result

    def schedule_idle(self, scheduler: Scheduler, *, delay_seconds: float = 300.0) -> None:
        async def _idle_cycle() -> None:
            await self.cycle(DecisionContext(is_idle=True, can_interrupt=True))
            self.schedule_idle(scheduler, delay_seconds=delay_seconds)

        scheduler.schedule_idle("autonomy.idle", delay_seconds, _idle_cycle)

    def _belief_snapshot(self, context: DecisionContext) -> BeliefSnapshot:
        mood = self.emotion.current_mood()
        claims = self.world.current_state()
        return BeliefSnapshot(
            world_claim_ids=[claim.id for claim in claims],
            mood_valence=mood.valence,
            mood_arousal=mood.arousal,
            mood_dominance=mood.dominance,
            context=context,
            observed_at=self.clock.now(),
        )

    def _collect_candidates(self, drives: list[DriveState], context: DecisionContext) -> list[DesireCandidate]:
        drive_map = {item.drive: item for item in drives}
        items: list[DesireCandidate] = []
        for bid in self.emotion.pending_bids(limit=100):
            items.append(self._candidate_from_bid(bid, drive_map, context))
        items.extend(self._drive_candidates(drive_map, context))
        return items

    def _candidate_from_bid(
        self,
        bid: SocialBid,
        drives: dict[DriveType, DriveState],
        context: DecisionContext,
    ) -> DesireCandidate:
        if bid.bid_type in {SocialBidType.SEEK_CONTACT, SocialBidType.SEEK_REPAIR}:
            primary = DriveType.SOCIAL
        else:
            primary = DriveType.EXPRESSION
        opportunity = self._target_opportunity(bid.target_id, context)
        interruption = self._interruption_penalty(context, bid.target_id)
        relationship = self._relationship_factor(bid.target_id)
        reward = {
            SocialBidType.EXPRESS_GRATITUDE: 0.78,
            SocialBidType.SEEK_CONTACT: 0.72,
            SocialBidType.SEEK_REPAIR: 0.84,
            SocialBidType.SET_BOUNDARY: 0.86,
            SocialBidType.WITHDRAW: 0.68,
        }[bid.bid_type]
        cost = {
            SocialBidType.EXPRESS_GRATITUDE: 0.06,
            SocialBidType.SEEK_CONTACT: 0.10,
            SocialBidType.SEEK_REPAIR: 0.16,
            SocialBidType.SET_BOUNDARY: 0.14,
            SocialBidType.WITHDRAW: 0.05,
        }[bid.bid_type]
        factors = UtilityFactors(
            drive_need=drives[primary].need,
            opportunity=opportunity,
            personality=1.0,
            emotion=_clamp(0.35 + 0.65 * bid.strength, 0.0, 1.0),
            relationship=relationship,
            expected_reward=reward,
            cost=cost,
            interruption_penalty=interruption,
        )
        utility = self._utility(factors)
        return DesireCandidate(
            kind=DesireKind.SOCIAL_BID,
            source_kind="social_bid",
            source_ref_id=str(bid.id),
            target_id=bid.target_id,
            primary_drive=primary,
            factors=factors,
            utility=utility,
            urgency=bid.strength,
            created_at=self.clock.now(),
            expires_at=bid.expires_at,
            metadata={"bid_type": bid.bid_type.value, "bid_reason": bid.reason},
        )

    def _drive_candidates(
        self,
        drives: dict[DriveType, DriveState],
        context: DecisionContext,
    ) -> list[DesireCandidate]:
        mapping = {
            DriveType.SOCIAL: (DesireKind.INITIATE_CONTACT, "PERSON_BK", 0.72, 0.10),
            DriveType.CURIOSITY: (DesireKind.ASK_QUESTION, None, 0.68, 0.08),
            DriveType.PLAY: (DesireKind.PLAY, "PERSON_BK", 0.62, 0.08),
            DriveType.NOVELTY: (DesireKind.SEEK_NOVELTY, None, 0.66, 0.10),
            DriveType.EXPRESSION: (DesireKind.EXPRESS, "PERSON_BK", 0.68, 0.08),
            DriveType.COMPETENCE: (DesireKind.PRACTICE, None, 0.64, 0.10),
            DriveType.REST: (DesireKind.REST, None, 0.82, 0.02),
        }
        candidates: list[DesireCandidate] = []
        for drive, state in drives.items():
            if state.need < 0.45:
                continue
            kind, target, reward, cost = mapping[drive]
            opportunity = self._target_opportunity(target, context) if target else context.opportunity
            interruption = self._interruption_penalty(context, target)
            if not context.is_idle and kind not in {DesireKind.REST}:
                interruption = _clamp(interruption + 0.18, 0.0, 1.0)
            factors = UtilityFactors(
                drive_need=state.need,
                opportunity=opportunity,
                personality=0.90,
                emotion=1.0,
                relationship=self._relationship_factor(target),
                expected_reward=reward,
                cost=cost,
                interruption_penalty=interruption,
            )
            utility = self._utility(factors)
            urgency = _clamp((state.need - 0.55) / 0.45, 0.0, 1.0)
            candidates.append(
                DesireCandidate(
                    kind=kind,
                    source_kind="drive",
                    source_ref_id=drive.value,
                    target_id=target,
                    primary_drive=drive,
                    factors=factors,
                    utility=utility,
                    urgency=urgency,
                    created_at=self.clock.now(),
                    expires_at=None,
                    metadata={"generated_from_homeostasis": True},
                )
            )
        return candidates

    @staticmethod
    def _utility(factors: UtilityFactors) -> float:
        product = (
            factors.drive_need
            * factors.opportunity
            * factors.personality
            * factors.emotion
            * factors.relationship
            * factors.expected_reward
        )
        return _clamp(product - factors.cost - factors.interruption_penalty, -1.0, 1.0)

    def _silence_utility(self, drives: list[DriveState], context: DecisionContext) -> float:
        drive_map = {item.drive: item.need for item in drives}
        social_pressure = max(drive_map.get(DriveType.SOCIAL, 0.0), drive_map.get(DriveType.EXPRESSION, 0.0))
        value = (
            0.16
            + 0.42 * drive_map.get(DriveType.REST, 0.0)
            + 0.45 * context.interruption_penalty
            + (0.32 if not context.can_interrupt else 0.0)
            + (0.12 if context.active_conversation else 0.0)
            - 0.10 * social_pressure
        )
        return _clamp(value, 0.0, 1.0)

    def _target_opportunity(self, target_id: str | None, context: DecisionContext) -> float:
        if target_id is None:
            return context.opportunity
        if target_id in context.busy_entities:
            return min(context.opportunity, 0.25)
        if context.available_entities and target_id not in context.available_entities:
            return min(context.opportunity, 0.20)
        return context.opportunity

    @staticmethod
    def _interruption_penalty(context: DecisionContext, target_id: str | None) -> float:
        penalty = context.interruption_penalty
        if not context.can_interrupt:
            penalty += 0.65
        elif context.active_conversation and target_id not in {None, context.active_addressee}:
            penalty += 0.28
        return _clamp(penalty, 0.0, 1.0)

    def _relationship_factor(self, target_id: str | None) -> float:
        if target_id is None or self.identities.get(target_id) is None:
            return 1.0
        affect = self.emotion.affect_for(target_id)
        salience = max(
            affect.affection,
            affect.gratitude,
            affect.anger,
            affect.irritation,
            affect.fear,
            abs(affect.trust - 0.5) * 2.0,
            0.5,
        )
        return _clamp(0.70 + 0.30 * salience, 0.0, 1.0)

    def _intention_from_candidate(self, candidate: DesireCandidate) -> Intention:
        now = self.clock.now()
        expires_at = candidate.expires_at or (now + self.DEFAULT_INTENTION_TTL)
        return Intention(
            kind=candidate.kind,
            target_id=candidate.target_id,
            source_kind=candidate.source_kind,
            source_ref_id=candidate.source_ref_id,
            selected_utility=candidate.utility,
            urgency=candidate.urgency,
            commitment_strength=_clamp(0.55 + 0.35 * candidate.urgency, 0.0, 1.0),
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            status=IntentionStatus.ACTIVE,
            metadata={
                "candidate_id": str(candidate.id),
                "candidate_metadata": candidate.metadata,
                "execution_deferred_to_v0_7": True,
            },
        )

    @staticmethod
    def _same_source(current: Intention, candidate: DesireCandidate) -> bool:
        return (
            current.kind == candidate.kind
            and current.source_kind == candidate.source_kind
            and current.source_ref_id == candidate.source_ref_id
            and current.target_id == candidate.target_id
        )
