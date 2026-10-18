from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "voice_kitchen"
    / "apply_signalsmith_candidates.py"
)
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_signalsmith", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_candidates_keep_pitch_small_and_move_formants_independently() -> None:
    candidates = MODULE.DEFAULT_CANDIDATES
    assert len(candidates) == 4
    assert any(c.pitch_semitones == 0.0 and c.formant_semitones > 0 for c in candidates)
    assert max(abs(c.pitch_semitones) for c in candidates) <= 0.5
    assert max(c.formant_semitones for c in candidates) <= 3.5
    assert all(c.formant_base_hz == 0.0 for c in candidates)


def test_command_uses_official_independent_pitch_and_formant_flags(tmp_path: Path) -> None:
    candidate = MODULE.DEFAULT_CANDIDATES[1]
    command = MODULE.signalsmith_command(
        tmp_path / "stretch",
        tmp_path / "in.wav",
        tmp_path / "out.wav",
        candidate,
    )
    assert "--semitones=0" in command
    assert "--formant=2.5" in command
    assert "--formant-base=0" in command
    assert "--block-ms=120" in command
    assert "--overlap=4" in command


def test_wav_duration_seconds(tmp_path: Path) -> None:
    wav_path = tmp_path / "one-second.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 24000)

    assert MODULE.wav_duration_seconds(wav_path) == 1.0
