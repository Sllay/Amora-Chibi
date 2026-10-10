from __future__ import annotations

from amora.emotion import MoodState

from .models import SpeechStyle, UtteranceKind


class LocalSpeechComposer:
    """Cheap deterministic speech for reflexes and already-resolved intentions."""

    def style_for_mood(self, mood: MoodState) -> SpeechStyle:
        return SpeechStyle(
            locale="pt-BR",
            rate=max(0.82, min(1.18, 1.0 + mood.arousal * 0.12)),
            pitch=max(0.9, min(1.1, 1.0 + mood.valence * 0.05)),
            volume=1.0,
            valence=mood.valence,
            arousal=mood.arousal,
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
        normalized = text.strip().lower()
        if "amora" in normalized and any(token in normalized for token in ("oi", "ei", "alô", "alo")):
            return "Oi, papis. Tô aqui."
        if normalized.endswith("?"):
            return "Uhum, tô ouvindo."
        return "Uhum."

    @staticmethod
    def kind_for_action(action_kind: str) -> UtteranceKind:
        return UtteranceKind.SEMANTIC
