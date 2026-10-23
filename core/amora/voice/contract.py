"""Versioned, minimal service payload. No history, identity or delivery metadata."""
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from amora.conversation.models import SpeechStyle, VoiceExpressionStyle
from .local_amora import MODEL_ID, VOICE_ID


class RenderExpression(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rate_scale: float = Field(default=1.0, ge=.90, le=1.08)
    pause_scale: float = Field(default=1.0, ge=.88, le=1.16)
    energy_scale: float = Field(default=1.0, ge=.88, le=1.06)
    pitch_range_scale: float = Field(default=1.0, ge=.90, le=1.08)
    f0_register_scale: float = Field(default=1.0, ge=.97, le=1.02)
    softness: float = Field(default=.25, ge=0, le=1)
    firmness: float = Field(default=.15, ge=0, le=1)


@dataclass(frozen=True)
class RenderInput:
    text: str
    style: SpeechStyle


class VoiceRenderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol: Literal['amora.voice.render.v1'] = 'amora.voice.render.v1'
    model: Literal['sherpa-onnx-supertonic-3-tts-int8-2026-05-11'] = MODEL_ID
    voice: Literal['AMORA_VOICE_SIGNATURE_V1_SOFT'] = VOICE_ID
    text: str = Field(min_length=1, max_length=800)
    rate: float = Field(default=1.0, ge=.5, le=2.)
    volume: float = Field(default=1.0, ge=0, le=1)
    expression: RenderExpression | None = None

    @classmethod
    def from_utterance(cls, item):
        expression = item.style.expression
        minimal = {key: getattr(expression,key) for key in RenderExpression.model_fields} if expression else None
        return cls(text=item.text, rate=item.style.rate, volume=item.style.volume, expression=minimal)

    def render_input(self):
        expression = VoiceExpressionStyle(**self.expression.model_dump()) if self.expression else None
        return RenderInput(self.text, SpeechStyle(rate=self.rate,volume=self.volume,expression=expression))
