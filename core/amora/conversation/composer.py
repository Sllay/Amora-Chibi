from __future__ import annotations

from amora.emotion import EmotionType, MoodState
from amora.voice_expression import VoiceProsodyPlanner

from .models import SpeechStyle, UtteranceKind, VoiceExpressionStyle


class LocalSpeechComposer:
    """Cheap deterministic speech for reflexes and already-resolved intentions."""

    def __init__(self) -> None:
        self.voice_prosody = VoiceProsodyPlanner()

    def style_for_mood(
        self,
        mood: MoodState,
        *,
        emotion: EmotionType | str | None = None,
        intensity: float = 0.0,
    ) -> SpeechStyle:
        resolved_emotion: EmotionType | None = None
        if emotion is not None:
            try:
                resolved_emotion = emotion if isinstance(emotion, EmotionType) else EmotionType(str(emotion))
            except ValueError:
                resolved_emotion = None
        expression = self.voice_prosody.safe_plan(
            mood,
            emotion=resolved_emotion,
            intensity=max(0.0, min(1.0, float(intensity))),
        )
        return SpeechStyle(
            locale="pt-BR",
            # Legacy fields stay available for old/non-candidate adapters, but the
            # Android Amora candidate consumes `expression` directly so it never
            # reclassifies emotion independently from Core.
            rate=max(0.82, min(1.18, 1.0 + mood.arousal * 0.12)),
            pitch=max(0.9, min(1.1, 1.0 + mood.valence * 0.05)),
            volume=1.0,
            valence=mood.valence,
            arousal=mood.arousal,
            dominance=mood.dominance,
            expression=VoiceExpressionStyle.model_validate(expression.model_dump(mode="json")),
        )

    def semantic_text(self, action_kind: str, target_id: str | None) -> str:
        papis = target_id == "PERSON_BK"
        mapping = {
            "initiate_contact": "Papis?" if papis else "Ei?",
            "ask_question": "Papis, posso te perguntar uma coisa?" if papis else "Posso te perguntar uma coisa?",
            "play": "Papis, bora fazer alguma coisa?" if papis else "Bora fazer alguma coisa?",
            "express": "Papis... eu queria te falar uma coisa." if papis else "Eu queria te falar uma coisa.",
            "seek_novelty": "Que tal a gente fazer algo diferente?",
            "practice": "Quero tentar uma coisa.",
            "observe": "Hmm...",
            "social_bid": "Papis?" if papis else "Ei?",
            "seek_repair": "Papis... posso tentar consertar isso com você?" if papis else "Posso tentar consertar isso com você?",
            "express_gratitude": "Obrigada, papis." if papis else "Obrigada.",
            "set_boundary": "Papis, isso me incomodou. Prefiro que a gente não faça assim." if papis else "Isso me incomodou. Prefiro que a gente não faça assim.",
            "withdraw": "Vou ficar quietinha um pouco, tá?" if papis else "Vou ficar quietinha um pouco.",
        }
        return mapping.get(action_kind, "Uhum?")

    def backchannel_text(self, text: str | None = None) -> str:
        normalized = (text or "").strip().lower()
        if normalized.endswith("?"):
            return "Hm?"
        if any(token in normalized for token in ("entendi", "entendeu", "sabe")):
            return "Uhum."
        return "Hmm."

    def fallback_dialogue(self, text: str, *, speaker_id: str | None = None) -> str:
        """Visible emergency response used only when normal cognition cannot answer.

        This must never look like an ordinary Amora utterance: operators need to
        recognize degraded execution directly on the messaging surface without
        opening infrastructure logs.
        """
        return "Oxe, deu ruim aqui e eu não consegui pensar na resposta. Tenta de novo? [AMORA:E_PROVIDER]"

    @staticmethod
    def operational_timeout_fallback() -> str:
        """Visible emergency response for a Core cognition hard timeout."""
        return "Oxe, meu cérebro travou e a resposta demorou demais. Tenta de novo? [AMORA:E_TIMEOUT]"

    @staticmethod
    def kind_for_action(action_kind: str) -> UtteranceKind:
        return UtteranceKind.SEMANTIC
