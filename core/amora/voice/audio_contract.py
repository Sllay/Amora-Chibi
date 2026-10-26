"""Small, dependency-free WAV contract shared by Core and worker renderers."""

from __future__ import annotations

import io
import math
import struct
import sys
import wave
from array import array
from collections.abc import Iterable

from .local_amora import LocalVoiceUnavailable

SAMPLE_RATE = 24000
MAX_WAV_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
MAX_CLIPPED_FRACTION = 0.01


def inspect_wav(
    body: bytes | bytearray,
    *,
    sample_rate: int = SAMPLE_RATE,
    min_seconds: float = 0,
    max_seconds: float = MAX_AUDIO_SECONDS,
) -> tuple[int, array]:
    """Reject malformed/truncated/oversized WAV and pathological saturation.

    PCM16 cannot encode NaN/Inf. Float synthesis is checked *before* encoding.
    No resampling, downmix, normalization or repair is performed here.
    """
    try:
        if not 44 <= len(body) <= MAX_WAV_BYTES:
            raise ValueError("size")
        if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
            raise ValueError("container")
        if struct.unpack_from("<I", body, 4)[0] + 8 != len(body):
            raise ValueError("truncated container")
        offset, seen = 12, set()
        while offset < len(body):
            if offset + 8 > len(body):
                raise ValueError("truncated chunk")
            name, size = struct.unpack_from("<4sI", body, offset)
            offset += 8
            end = offset + size
            if end + size % 2 > len(body):
                raise ValueError("truncated chunk")
            if name in {b"fmt ", b"data"}:
                if name in seen:
                    raise ValueError("duplicate audio chunk")
                seen.add(name)
            if name == b"fmt ":
                if size < 16 or struct.unpack_from("<HHIIHH", body, offset) != (
                    1,
                    1,
                    sample_rate,
                    sample_rate * 2,
                    2,
                    16,
                ):
                    raise ValueError("invalid PCM format")
            if name == b"data" and (size == 0 or size % 2):
                raise ValueError("invalid PCM alignment")
            offset = end + size % 2
        if seen != {b"fmt ", b"data"}:
            raise ValueError("missing audio chunks")
        with wave.open(io.BytesIO(body), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != sample_rate
                or wav.getcomptype() != "NONE"
            ):
                raise ValueError("format")
            frames = wav.getnframes()
            duration = frames / sample_rate
            if frames <= 0 or not min_seconds <= duration <= max_seconds:
                raise ValueError("duration")
            raw = wav.readframes(frames)
            if len(raw) != frames * 2:
                raise ValueError("truncated samples")
        samples = array("h", raw)
        if sys.byteorder != "little":
            samples.byteswap()
        if sum(abs(v) >= 32760 for v in samples) / frames >= MAX_CLIPPED_FRACTION:
            raise ValueError("clipping")
        return sample_rate, samples
    except (wave.Error, EOFError, ValueError, struct.error, ZeroDivisionError) as exc:
        raise LocalVoiceUnavailable("VOICE_INVALID_WAV") from exc


def validate_wav(body: bytes | bytearray) -> tuple[int, int]:
    rate, samples = inspect_wav(body)
    return rate, len(samples)


def encode_pcm16(samples: Iterable[float], sample_rate: int) -> bytes:
    """Validate full-precision output and encode the transport, without DSP."""
    if sample_rate != SAMPLE_RATE:
        raise LocalVoiceUnavailable("VOICE_INVALID_SAMPLE_RATE")
    pcm = array("h")
    clipped = 0
    for value in samples:
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise LocalVoiceUnavailable("VOICE_INVALID_SAMPLES") from exc
        if not math.isfinite(value) or abs(value) > 1.0:
            raise LocalVoiceUnavailable("VOICE_INVALID_SAMPLES")
        clipped += abs(value) >= 0.9998
        pcm.append(round(value * 32767))
        if len(pcm) > SAMPLE_RATE * MAX_AUDIO_SECONDS:
            raise LocalVoiceUnavailable("VOICE_AUDIO_TOO_LARGE")
    if not pcm or clipped / len(pcm) >= MAX_CLIPPED_FRACTION:
        raise LocalVoiceUnavailable("VOICE_INVALID_SAMPLES")
    if sys.byteorder != "little":
        pcm.byteswap()
    result = io.BytesIO()
    with wave.open(result, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
    body = result.getvalue()
    validate_wav(body)
    return body
