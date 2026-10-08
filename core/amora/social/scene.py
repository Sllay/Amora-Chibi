from __future__ import annotations

from pydantic import BaseModel, Field


class ParticipantState(BaseModel):
    entity_id: str
    speaking: bool = False
    listening: bool = True
    attention_target: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CurrentSocialScene(BaseModel):
    participants: dict[str, ParticipantState] = Field(default_factory=dict)
    current_speaker: str | None = None
    active_addressee: str | None = None
    conversation_floor: str | None = None
    listeners: set[str] = Field(default_factory=set)
    attention_targets: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SocialSceneTracker:
    def __init__(self, scene: CurrentSocialScene | None = None) -> None:
        self.scene = scene or CurrentSocialScene()

    def ensure_participant(self, entity_id: str, *, confidence: float = 1.0) -> ParticipantState:
        participant = self.scene.participants.get(entity_id)
        if participant is None:
            participant = ParticipantState(entity_id=entity_id, confidence=confidence)
            self.scene.participants[entity_id] = participant
        else:
            participant.confidence = max(participant.confidence, confidence)
        return participant

    def set_attention(self, source_id: str, target_id: str) -> None:
        self.ensure_participant(source_id).attention_target = target_id
        self.scene.attention_targets[source_id] = target_id

    def begin_turn(
        self,
        speaker_id: str,
        *,
        addressee_id: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        for participant in self.scene.participants.values():
            participant.speaking = False
        speaker = self.ensure_participant(speaker_id, confidence=confidence)
        speaker.speaking = True
        self.scene.current_speaker = speaker_id
        self.scene.conversation_floor = speaker_id
        self.scene.active_addressee = addressee_id
        self.scene.confidence = confidence
        if addressee_id:
            self.ensure_participant(addressee_id)
            self.set_attention(speaker_id, addressee_id)
            self.scene.listeners.add(addressee_id)

    def update_addressee(self, addressee_id: str | None, *, confidence: float) -> None:
        self.scene.active_addressee = addressee_id
        self.scene.confidence = confidence
        if self.scene.current_speaker and addressee_id:
            self.ensure_participant(addressee_id)
            self.set_attention(self.scene.current_speaker, addressee_id)
            self.scene.listeners.add(addressee_id)

    def end_turn(self, speaker_id: str) -> None:
        participant = self.scene.participants.get(speaker_id)
        if participant:
            participant.speaking = False
        if self.scene.current_speaker == speaker_id:
            self.scene.current_speaker = None
        if self.scene.conversation_floor == speaker_id:
            self.scene.conversation_floor = None
