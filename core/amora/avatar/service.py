from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from amora.conversation import ConversationEngine
from amora.emotion import EmotionEngine
from amora.events import AmoraEvent
from amora.social import SocialSceneTracker
from amora.time import Clock

from .models import AvatarProject, AvatarSemanticState
from .store import AvatarStore


@dataclass(slots=True)
class _TransientCue:
    gesture: str | None
    attention_target: str | None
    expires_at: datetime
    reason_code: str


_GESTURE_BY_ACTION = {
    "initiate_contact": "attention",
    "seek_contact": "attention",
    "express": "express",
    "ask_question": "curious_tilt",
    "play": "playful",
    "seek_novelty": "curious_tilt",
    "practice": "focus",
    "social_bid": "social",
    "set_boundary": "boundary",
    "withdraw": "look_away",
    "seek_repair": "soften",
    "express_gratitude": "gratitude",
}


class AvatarEngine:
    """Provider-independent semantic bridge between Amora's mind and the web avatar."""

    def __init__(
        self,
        store: AvatarStore,
        clock: Clock,
        emotion: EmotionEngine,
        conversation: ConversationEngine,
        social: SocialSceneTracker,
    ) -> None:
        self.store = store
        self.clock = clock
        self.emotion = emotion
        self.conversation = conversation
        self.social = social
        self._cue: _TransientCue | None = None

    async def handle_semantic_event(self, event: AmoraEvent) -> None:
        action_kind = str(event.payload.get("action_kind") or "")
        gesture = _GESTURE_BY_ACTION.get(action_kind)
        if gesture is None:
            return
        self._cue = _TransientCue(
            gesture=gesture,
            attention_target=event.target,
            expires_at=self.clock.now() + timedelta(seconds=3),
            reason_code=f"ACTION_{action_kind.upper()}",
        )

    def project(self) -> AvatarProject:
        return self.store.get_project()

    def save_project(self, project: AvatarProject) -> AvatarProject:
        return self.store.put_project(project)

    def semantic_state(self) -> AvatarSemanticState:
        now = self.clock.now()
        mood = self.emotion.current_mood()
        valence = max(-1.0, min(1.0, float(mood.valence)))
        arousal = max(-1.0, min(1.0, float(mood.arousal)))
        abs_v = abs(valence)
        intensity = max(abs_v, max(0.0, arousal))

        if valence >= 0.28:
            emotion = "happy"
            expression = "happy"
        elif valence <= -0.32 and arousal >= 0.28:
            emotion = "annoyed"
            expression = "angry"
        elif valence <= -0.22:
            emotion = "sad"
            expression = "sad"
        elif arousal >= 0.62:
            emotion = "alert"
            expression = "neutral"
        else:
            emotion = "neutral"
            expression = "neutral"

        speaking = any(item.status.value == "claimed" for item in self.conversation.store.list_utterances(limit=8))
        mouth = "talk" if speaking else "neutral"
        attention_target = self.social.scene.active_addressee
        gesture: str | None = None
        reasons = ["MOOD_TO_EXPRESSION"]

        cue = self._cue
        if cue is not None:
            if cue.expires_at > now:
                gesture = cue.gesture
                attention_target = cue.attention_target or attention_target
                reasons.append(cue.reason_code)
            else:
                self._cue = None

        blush = 0.0
        if valence > 0.22:
            blush = min(0.65, max(0.0, valence * 0.8))

        if speaking:
            reasons.append("SPEECH_CLAIM_ACTIVE")

        return AvatarSemanticState(
            timestamp=now,
            expression=expression,
            emotion=emotion,
            intensity=max(0.0, min(1.0, intensity)),
            gesture=gesture,
            attention_target=attention_target,
            speaking=speaking,
            mouth=mouth,
            blink_allowed=True,
            blush=blush,
            arousal=arousal,
            valence=valence,
            reason_codes=reasons,
        )

    def health(self) -> dict[str, object]:
        state = self.semantic_state()
        return {
            "avatar_project_count": self.store.project_count(),
            "avatar_asset_count": self.store.asset_count(),
            "avatar_expression": state.expression,
            "avatar_speaking": state.speaking,
        }

    def close(self) -> None:
        self.store.close()
