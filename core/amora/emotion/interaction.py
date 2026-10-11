from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from amora.events import PrivacyScope

from .models import AppraisalInput


@dataclass(frozen=True, slots=True)
class InteractionAppraisalProfile:
    label: str
    outcome_valence: float
    goal_relevance: float
    novelty: float
    expectedness: float
    agency: float
    control: float
    intentionality: float
    norm_violation: float
    relationship_relevance: float


class InteractionAppraiser:
    """Conservative deterministic appraisal for observed social utterances.

    This is deliberately narrower than sentiment analysis. It only emits strong
    appraisal dimensions when the utterance contains evidence about how the
    speaker is treating Amora/the relationship. Neutral questions and topic talk
    remain close to baseline so the Emotion Engine does not manufacture feelings
    from every sentence.
    """

    _THREAT_RE = re.compile(
        r"\b(vou|vo|eu vou)\s+(te\s+)?(apagar|deletar|desligar|desinstalar|quebrar|matar)\b|"
        r"\b(te apago|te deleto|te desligo|vou acabar com voce)\b"
    )
    _HOSTILITY_RE = re.compile(
        r"\b(voce|vc|tu|amora)\b.{0,24}\b(idiota|burra|burro|inutil|lixo|ridicula|ridiculo|"
        r"chata|chato|insuportavel|nojenta|nojento|horrivel)\b|"
        r"\b(cala a boca|te odeio|odeio voce|odeio vc|odeio tu)\b"
    )
    _AFFECTION_RE = re.compile(
        r"\b(te amo|amo voce|amo vc|amo tu|gosto de voce|gosto de vc|gosto de tu|"
        r"voce e (?:muito )?(?:fofa|linda|incrivel|legal|especial)|"
        r"tu e (?:muito )?(?:fofa|linda|incrivel|legal|especial))\b"
    )
    _GRATITUDE_RE = re.compile(
        r"\b(obrigad[oa]|valeu|agradeco|agradeço|muito obrigado|muito obrigada)\b"
    )
    _APOLOGY_RE = re.compile(
        r"\b(desculpa|desculpe|foi mal|perdao|perdão|me perdoa|me perdoe)\b"
    )
    _REJECTION_RE = re.compile(
        r"\b(nao gosto de voce|não gosto de você|nao gosto de vc|nao gosto de tu|"
        r"voce me decepcionou|você me decepcionou|to decepcionad[oa] com voce|"
        r"estou decepcionad[oa] com voce)\b"
    )

    _NEUTRAL = InteractionAppraisalProfile(
        label="neutral_social_turn",
        outcome_valence=0.0,
        goal_relevance=0.22,
        novelty=0.18,
        expectedness=0.72,
        agency=0.55,
        control=0.72,
        intentionality=0.55,
        norm_violation=0.0,
        relationship_relevance=0.38,
    )
    _GRATITUDE = InteractionAppraisalProfile(
        label="gratitude_toward_amora",
        outcome_valence=0.72,
        goal_relevance=0.62,
        novelty=0.28,
        expectedness=0.58,
        agency=0.88,
        control=0.82,
        intentionality=0.90,
        norm_violation=0.0,
        relationship_relevance=0.88,
    )
    _AFFECTION = InteractionAppraisalProfile(
        label="affection_toward_amora",
        outcome_valence=0.82,
        goal_relevance=0.72,
        novelty=0.34,
        expectedness=0.55,
        agency=0.90,
        control=0.80,
        intentionality=0.92,
        norm_violation=0.0,
        relationship_relevance=0.96,
    )
    _APOLOGY = InteractionAppraisalProfile(
        label="relationship_repair_signal",
        outcome_valence=0.42,
        goal_relevance=0.68,
        novelty=0.34,
        expectedness=0.48,
        agency=0.84,
        control=0.72,
        intentionality=0.86,
        norm_violation=0.0,
        relationship_relevance=0.94,
    )
    _HOSTILITY = InteractionAppraisalProfile(
        label="hostility_toward_amora",
        outcome_valence=-0.78,
        goal_relevance=0.78,
        novelty=0.42,
        expectedness=0.32,
        agency=0.94,
        control=0.64,
        intentionality=0.90,
        norm_violation=0.90,
        relationship_relevance=0.92,
    )
    _THREAT = InteractionAppraisalProfile(
        label="threat_toward_amora",
        outcome_valence=-0.92,
        goal_relevance=0.94,
        novelty=0.72,
        expectedness=0.18,
        agency=0.96,
        control=0.18,
        intentionality=0.94,
        norm_violation=0.96,
        relationship_relevance=0.90,
    )
    _REJECTION = InteractionAppraisalProfile(
        label="relationship_rejection",
        outcome_valence=-0.62,
        goal_relevance=0.72,
        novelty=0.48,
        expectedness=0.36,
        agency=0.88,
        control=0.52,
        intentionality=0.84,
        norm_violation=0.46,
        relationship_relevance=0.96,
    )

    def appraise_utterance(
        self,
        text: str,
        *,
        speaker_id: str,
        addressee_id: str | None,
        source_event_id: UUID | None,
        privacy_scope: PrivacyScope,
        dedupe_key: str | None = None,
    ) -> AppraisalInput:
        normalized = _normalize(text)
        profile = self._profile(normalized, directed=addressee_id == "ENTITY_AMORA")
        directed = addressee_id == "ENTITY_AMORA"

        relationship_relevance = profile.relationship_relevance
        goal_relevance = profile.goal_relevance
        if not directed:
            relationship_relevance *= 0.20
            goal_relevance *= 0.45

        return AppraisalInput(
            description=f"conversation:{profile.label}",
            outcome_valence=profile.outcome_valence if directed else profile.outcome_valence * 0.25,
            target_id=speaker_id if directed else None,
            agent_id=speaker_id,
            cause_event_id=source_event_id,
            goal_relevance=goal_relevance,
            novelty=profile.novelty,
            expectedness=profile.expectedness,
            agency=profile.agency,
            control=profile.control,
            intentionality=profile.intentionality,
            norm_violation=profile.norm_violation if directed else profile.norm_violation * 0.20,
            relationship_relevance=relationship_relevance,
            privacy_scope=privacy_scope,
            dedupe_key=dedupe_key or (f"interaction-appraisal:{source_event_id}" if source_event_id else None),
            metadata={
                "appraisal_source": "local_interaction_appraiser",
                "profile": profile.label,
                "directed_to_amora": directed,
            },
        )

    def _profile(self, text: str, *, directed: bool) -> InteractionAppraisalProfile:
        if not directed:
            return self._NEUTRAL
        if self._THREAT_RE.search(text):
            return self._THREAT
        if self._HOSTILITY_RE.search(text):
            return self._HOSTILITY
        if self._REJECTION_RE.search(text):
            return self._REJECTION
        if self._APOLOGY_RE.search(text):
            return self._APOLOGY
        if self._AFFECTION_RE.search(text):
            return self._AFFECTION
        if self._GRATITUDE_RE.search(text):
            return self._GRATITUDE
        return self._NEUTRAL


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", without_marks)
    return re.sub(r"\s+", " ", cleaned).strip()
