from __future__ import annotations

from amora.events import PrivacyScope

from .models import MemoryCandidate, MemoryKind, MemoryWriteDecision, ProvenanceKind


class MemoryWriteGate:
    WEIGHTS: dict[str, float] = {
        "novelty": 0.20,
        "importance": 0.20,
        "emotional_weight": 0.12,
        "relationship_relevance": 0.12,
        "future_consequence": 0.12,
        "rarity": 0.08,
        "goal_relevance": 0.10,
        "repetition": 0.06,
    }

    THRESHOLDS: dict[MemoryKind, float] = {
        MemoryKind.EPISODIC: 0.38,
        MemoryKind.SEMANTIC: 0.48,
        MemoryKind.RELATIONAL: 0.42,
    }

    def evaluate(self, candidate: MemoryCandidate) -> MemoryWriteDecision:
        if candidate.privacy_scope == PrivacyScope.AUTH:
            return MemoryWriteDecision(
                store=False,
                score=0.0,
                threshold=1.0,
                reason_codes=["AUTH_NEVER_COGNITIVE_MEMORY"],
            )

        score = sum(float(getattr(candidate, name)) * weight for name, weight in self.WEIGHTS.items())
        score = max(0.0, min(1.0, score))
        threshold = self.THRESHOLDS[candidate.kind]
        reasons: list[str] = []
        recommended_kind: MemoryKind | None = candidate.kind

        if candidate.provenance.kind in {ProvenanceKind.INFERENCE, ProvenanceKind.DERIVED}:
            reasons.append("PROVENANCE_DERIVED")
            if candidate.kind == MemoryKind.SEMANTIC and candidate.provenance.confidence < 0.90:
                recommended_kind = MemoryKind.EPISODIC
                threshold = self.THRESHOLDS[MemoryKind.EPISODIC]
                reasons.append("LOW_CONFIDENCE_SEMANTIC_DOWNGRADED_TO_EPISODIC")

        if candidate.privacy_scope in {PrivacyScope.PRIVATE, PrivacyScope.SECRET}:
            reasons.append("SENSITIVE_MEMORY")
        if candidate.importance >= 0.75:
            reasons.append("HIGH_IMPORTANCE")
        if candidate.relationship_relevance >= 0.70:
            reasons.append("RELATIONSHIP_RELEVANT")
        if candidate.future_consequence >= 0.70:
            reasons.append("FUTURE_CONSEQUENCE")
        if candidate.novelty >= 0.75:
            reasons.append("HIGH_NOVELTY")
        if candidate.repetition >= 0.75:
            reasons.append("REPEATED_SIGNAL")

        store = score >= threshold
        reasons.append("WRITE_GATE_ACCEPT" if store else "WRITE_GATE_REJECT_LOW_SIGNAL")
        return MemoryWriteDecision(
            store=store,
            score=round(score, 6),
            threshold=threshold,
            reason_codes=reasons,
            recommended_kind=recommended_kind if store else None,
        )
