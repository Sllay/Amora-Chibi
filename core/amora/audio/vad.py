from __future__ import annotations

import math
import struct

from .models import VoiceActivityResult


class EnergyVad:
    """Dependency-free PCM16 fallback VAD.

    This is intentionally a cheap fallback, not the final acoustic model. The
    Android client can provide sherpa-onnx/Silero VAD decisions later without
    changing the Core contract.
    """

    def __init__(self, *, rms_threshold: int = 500) -> None:
        if rms_threshold <= 0:
            raise ValueError("rms_threshold must be positive")
        self.rms_threshold = rms_threshold

    def detect_pcm16(self, pcm: bytes, *, sample_rate_hz: int) -> VoiceActivityResult:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        usable = len(pcm) - (len(pcm) % 2)
        if usable < 2:
            return VoiceActivityResult(
                speech=False,
                score=0.0,
                reason_codes=["EMPTY_OR_TOO_SHORT"],
            )

        total = 0
        count = 0
        for (sample,) in struct.iter_unpack("<h", pcm[:usable]):
            total += sample * sample
            count += 1
        rms = math.isqrt(total // count) if count else 0
        ratio = rms / self.rms_threshold
        score = max(0.0, min(1.0, ratio / 2.0))
        return VoiceActivityResult(
            speech=rms >= self.rms_threshold,
            score=score,
            reason_codes=[f"PCM16_RMS_{rms}", "ENERGY_THRESHOLD"],
        )
