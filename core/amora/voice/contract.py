"""Versioned, minimal service payload. No history, identity or delivery metadata."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field
from amora.conversation.models import SpeechStyle, VoiceExpressionStyle
from .local_amora import MODEL_ID, VOICE_ID


class RenderExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    pause_scale: float = Field(default=1.0, ge=0.88, le=1.16)
    energy_scale: float = Field(default=1.0, ge=0.88, le=1.06)
    pitch_range_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    f0_register_scale: float = Field(default=1.0, ge=0.97, le=1.02)
    softness: float = Field(default=0.25, ge=0, le=1)
    firmness: float = Field(default=0.15, ge=0, le=1)


@dataclass(frozen=True)
class RenderInput:
    text: str
    style: SpeechStyle
    cues: tuple[str, ...] = ()


class VoiceRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["amora.voice.render.v1"] = "amora.voice.render.v1"
    model: Literal["sherpa-onnx-supertonic-3-tts-int8-2026-05-11"] = MODEL_ID
    voice: Literal["AMORA_VOICE_SIGNATURE_V1_SOFT"] = VOICE_ID
    text: str = Field(min_length=1, max_length=800)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0, le=1)
    expression: RenderExpression | None = None

    @classmethod
    def from_utterance(cls, item):
        expression = item.style.expression
        minimal = (
            {key: getattr(expression, key) for key in RenderExpression.model_fields}
            if expression
            else None
        )
        return cls(
            text=item.text,
            rate=item.style.rate,
            volume=item.style.volume,
            expression=minimal,
        )

    def render_input(self):
        expression = (
            VoiceExpressionStyle(**self.expression.model_dump())
            if self.expression
            else None
        )
        return RenderInput(
            self.text,
            SpeechStyle(rate=self.rate, volume=self.volume, expression=expression),
        )


class RenderIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    utterance_id: UUID
    turn_id: UUID | None = None


Cue = Literal["LAUGHTER", "SIGH", "SURPRISE", "DISSATISFACTION"]


class VoiceRenderRequestV2(BaseModel):
    """Renderer-neutral synthesis input; renderer/reference/tuning are forbidden."""

    model_config = ConfigDict(extra="forbid")
    protocol: Literal["amora.voice.render.v2"]
    text: str = Field(min_length=1, max_length=800)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0, le=1)
    expression: RenderExpression | None = None
    cues: list[Cue] = Field(default_factory=list, max_length=4)
    identity: RenderIdentity

    @classmethod
    def from_utterance(cls, item):
        expression = item.style.expression
        minimal = (
            {key: getattr(expression, key) for key in RenderExpression.model_fields}
            if expression
            else None
        )
        turn = item.metadata.get("cognitive_turn_id")
        return cls(
            protocol="amora.voice.render.v2",
            text=item.text,
            rate=item.style.rate,
            volume=item.style.volume,
            expression=minimal,
            cues=item.metadata.get("cues", []),
            identity=RenderIdentity(
                request_id=uuid4(),
                utterance_id=item.id,
                turn_id=UUID(str(turn)) if turn else None,
            ),
        )

    def render_input(self) -> RenderInput:
        expression = (
            VoiceExpressionStyle(**self.expression.model_dump())
            if self.expression
            else None
        )
        return RenderInput(
            self.text,
            SpeechStyle(rate=self.rate, volume=self.volume, expression=expression),
            tuple(self.cues),
        )
