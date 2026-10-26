"""HTTPS-only synthesis client. No model loading, fallback or render retry."""

from __future__ import annotations

import math
import os

import httpx

from amora.events import PrivacyScope
from amora.observability import remember_secret
from amora.security import validate_remote_origin
from .audio_contract import MAX_WAV_BYTES, validate_wav
from .contract import VoiceRenderRequest, VoiceRenderRequestV2
from .identity import WorkerVoiceIdentity
from .local_amora import (
    LocalVoiceAudio,
    LocalVoiceUnavailable,
    MODEL_ID,
    NUM_STEPS,
    VOICE_ID,
)


class RemoteVoiceBackend:
    def __init__(
        self,
        url,
        token,
        *,
        transport=None,
        timeout=20.0,
        protocol="amora.voice.render.v1",
        expected_identity: WorkerVoiceIdentity | None = None,
    ):
        self.url = validate_remote_origin(url).rstrip("/")
        if not token:
            raise ValueError("voice service token is required")
        if protocol not in {"amora.voice.render.v1", "amora.voice.render.v2"}:
            raise ValueError("VOICE_PROTOCOL_UNSUPPORTED")
        if protocol.endswith(".v2") and expected_identity is None:
            raise ValueError("V2 requires an explicitly pinned worker identity")
        if protocol.endswith(".v1") and expected_identity is not None:
            raise ValueError("V1 identity is the frozen Supertonic baseline")
        if not math.isfinite(timeout) or not 0 < timeout <= 80:
            raise ValueError("invalid voice timeout")
        self.protocol = protocol
        self.identity = expected_identity or WorkerVoiceIdentity(
            "supertonic", MODEL_ID, VOICE_ID
        )
        self._token = token
        remember_secret(token)
        self.client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False
        )
        self._available = False
        self._last_error = None

    @classmethod
    def from_environment(cls, *, transport=None):
        protocol = os.getenv("AMORA_VOICE_PROTOCOL", "amora.voice.render.v1")
        identity = None
        if protocol == "amora.voice.render.v2":
            identity = WorkerVoiceIdentity(
                os.getenv("AMORA_VOICE_EXPECTED_RENDERER", ""),
                os.getenv("AMORA_VOICE_EXPECTED_MODEL", ""),
                os.getenv("AMORA_VOICE_EXPECTED_VOICE", ""),
            )
        return cls(
            os.getenv("AMORA_VOICE_WORKER_URL", ""),
            os.getenv("AMORA_VOICE_SERVICE_TOKEN", ""),
            transport=transport,
            timeout=float(os.getenv("AMORA_VOICE_TIMEOUT_SECONDS", "20")),
            protocol=protocol,
            expected_identity=identity,
        )

    def state(self):
        state = {
            "provider": "amora_remote_supertonic_world"
            if self.protocol.endswith(".v1")
            else "amora_remote_voice",
            "remote": True,
            "protocol": self.protocol,
            "renderer": self.identity.renderer,
            "model": self.identity.model,
            "voice": self.identity.voice,
            "streaming_capable": self.identity.streaming_capable,
            "configured": True,
            "available": self._available,
            "last_error": self._last_error,
            "zero_cost_required": True,
        }
        if self.protocol.endswith(".v1"):
            state["num_steps"] = NUM_STEPS
        return state

    def render(self, item):
        if item.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            raise LocalVoiceUnavailable("VOICE_PRIVACY_DENIED")
        try:
            payload = (
                VoiceRenderRequestV2
                if self.protocol.endswith(".v2")
                else VoiceRenderRequest
            ).from_utterance(item)
            # One HTTP attempt. No redirect, fallback, ticket, replay or retry.
            with self.client.stream(
                "POST",
                self.url + "/v1/render",
                headers={"Authorization": "Bearer " + self._token},
                json=payload.model_dump(mode="json"),
            ) as response:
                if response.status_code != 200:
                    raise LocalVoiceUnavailable("VOICE_WORKER_UNAVAILABLE")
                self._validate_identity(response, payload)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_WAV_BYTES:
                        raise LocalVoiceUnavailable("VOICE_AUDIO_TOO_LARGE")
                rate, frames = validate_wav(body)
                seconds = float(response.headers.get("X-Amora-Render-Seconds", "0"))
                if not math.isfinite(seconds) or seconds < 0:
                    raise LocalVoiceUnavailable("VOICE_INVALID_METRICS")
            self._available, self._last_error = True, None
            return LocalVoiceAudio(bytes(body), rate, frames, seconds, 0.0)
        except (httpx.HTTPError, ValueError, LocalVoiceUnavailable) as exc:
            self._available = False
            self._last_error = (
                str(exc)
                if isinstance(exc, LocalVoiceUnavailable)
                else "VOICE_TRANSPORT_FAILED"
            )
            raise LocalVoiceUnavailable(self._last_error) from exc

    def _validate_identity(self, response, payload):
        if isinstance(payload, VoiceRenderRequestV2):
            expected = self.identity.headers()
            expected.pop("X-Amora-TTS-Model")
            expected.update(
                {
                    "X-Amora-Protocol": payload.protocol,
                    "X-Amora-Request-Id": str(payload.identity.request_id),
                    "X-Amora-Utterance-Id": str(payload.identity.utterance_id),
                    "X-Amora-Turn-Id": str(payload.identity.turn_id)
                    if payload.identity.turn_id
                    else "none",
                }
            )
            if any(
                response.headers.get(key) != value for key, value in expected.items()
            ):
                raise LocalVoiceUnavailable("VOICE_IDENTITY_MISMATCH")
            if response.headers.get("content-type", "").split(";")[0] != "audio/wav":
                raise LocalVoiceUnavailable("VOICE_INVALID_WAV")
            if "X-Amora-Render-Seconds" not in response.headers:
                raise LocalVoiceUnavailable("VOICE_INVALID_METRICS")
        elif (
            response.headers.get("X-Amora-Voice") != VOICE_ID
            or response.headers.get("X-Amora-TTS-Model") != MODEL_ID
        ):
            raise LocalVoiceUnavailable("VOICE_IDENTITY_MISMATCH")

    def close(self):
        self.client.close()
