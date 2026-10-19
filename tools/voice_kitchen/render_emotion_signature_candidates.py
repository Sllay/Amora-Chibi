#!/usr/bin/env python3
"""Render Emotion/Prosody V2 auditions on top of Amora VoiceSignature V1.

BK approved the identity and signature, but the first emotion pass was too subtle.
V2 therefore makes delivery cues easier to hear while keeping identity frozen:
- rate/cadence is stronger for tender/sad/angry speech;
- F0 contour range remains bounded;
- a tiny emotional F0-register shift is allowed (0.97..1.02);
- energy is more clearly differentiated;
- softness/firmness drive a small post-signature tone shelf, RMS matched.

No formant/VTL change, speaker/style change, or VoiceSignature texture change is
allowed by emotion.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CORE_ROOT = REPO_ROOT / "core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from amora.emotion import EmotionType, EntityAffect, MoodState  # noqa: E402
from amora.voice.expression import VoiceProsodyPlanner  # noqa: E402
from mix_supertonic_styles import generate_candidates  # noqa: E402


FINAL_PATH = HERE / "apply_world_final_ab.py"
FINAL_SPEC = importlib.util.spec_from_file_location("amora_final_b", FINAL_PATH)
assert FINAL_SPEC is not None and FINAL_SPEC.loader is not None
FINAL = importlib.util.module_from_spec(FINAL_SPEC)
sys.modules[FINAL_SPEC.name] = FINAL
FINAL_SPEC.loader.exec_module(FINAL)

SIGNATURE_PATH = HERE / "apply_amora_signature_v1.py"
SIG_SPEC = importlib.util.spec_from_file_location("amora_signature_v1", SIGNATURE_PATH)
assert SIG_SPEC is not None and SIG_SPEC.loader is not None
SIGNATURE = importlib.util.module_from_spec(SIG_SPEC)
sys.modules[SIG_SPEC.name] = SIGNATURE
SIG_SPEC.loader.exec_module(SIGNATURE)

WorldCandidate = FINAL.WorldCandidate
transform_world = FINAL.transform_world
FINAL_B = FINAL.FINAL_CANDIDATES[1]
apply_soft_harmonic_texture = SIGNATURE.apply_soft_harmonic_texture

PHRASES = (
    "Bom dia. Como você dormiu?",
    "Eu realmente não concordo com isso.",
    "Você deixou suas coisas espalhadas pela sala.",
    "Amor, você sabe que isso não faz o menor sentido, né?",
)


def _mood(valence: float, arousal: float, dominance: float) -> MoodState:
    return MoodState(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        updated_at=datetime.now(UTC),
    )


def _affect(**values: float) -> EntityAffect:
    return EntityAffect(
        entity_id="PERSON_BK",
        updated_at=datetime.now(UTC),
        **values,
    )


# These are audition scenarios, not hard-coded emotion states for production.
# Production will feed the planner from the real Core mood/event/affect values.
PRESETS = (
    {
        "id": "NEUTRA",
        "mood": _mood(0.0, 0.0, 0.0),
        "emotion": None,
        "intensity": 0.0,
        "affect": None,
    },
    {
        "id": "FELIZ",
        "mood": _mood(0.65, 0.45, 0.18),
        "emotion": EmotionType.JOY,
        "intensity": 0.75,
        "affect": None,
    },
    {
        "id": "CARINHOSA",
        "mood": _mood(0.55, 0.16, 0.0),
        "emotion": EmotionType.GRATITUDE,
        "intensity": 0.70,
        "affect": _affect(affection=0.85, gratitude=0.45, trust=0.80),
    },
    {
        "id": "BRAVA",
        "mood": _mood(-0.65, 0.78, 0.55),
        "emotion": EmotionType.ANGER,
        "intensity": 0.90,
        "affect": _affect(anger=0.75, irritation=0.60, trust=0.35),
    },
    {
        "id": "TRISTE",
        "mood": _mood(-0.65, 0.10, -0.30),
        "emotion": EmotionType.DISAPPOINTMENT,
        "intensity": 0.90,
        "affect": None,
    },
)


def _signature_candidate(*, pitch_range_scale: float, f0_register_scale: float) -> WorldCandidate:
    # FINAL_B morphology and Signature V1 remain fixed. Expression may only move
    # the F0 register a few percent and widen/narrow its contour.
    return WorldCandidate(
        f"AMORA_EXPRESSION_V2_{pitch_range_scale:.4f}_{f0_register_scale:.4f}",
        FINAL_B.target_f0_median_hz * f0_register_scale,
        FINAL_B.max_f0_ratio,
        FINAL_B.pitch_range_scale * pitch_range_scale,
        FINAL_B.spectral_ratios,
        SIGNATURE.APERIODICITY_SCALE,
        FINAL_B.high_shelf_db,
    )


def _apply_expression_tone(samples, sample_rate: int, *, softness: float, firmness: float):
    """Apply a tiny RMS-matched brightness shelf from the expression plan.

    This is not a new timbre identity layer. It is a bounded proxy for voice
    quality in the audition pack: tender delivery softens the upper spectrum a
    little; firm/angry delivery gives consonants slightly more presence.
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float64)
    if x.size == 0:
        return x.copy(), 0.0

    delta_db = 0.90 * (float(firmness) - 0.15) - 1.10 * (float(softness) - 0.25)
    delta_db = float(max(-0.70, min(0.80, delta_db)))
    if abs(delta_db) < 1e-6:
        return x.copy(), 0.0

    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / float(sample_rate))
    start_hz = 1800.0
    full_hz = 5000.0
    t = np.clip((freqs - start_hz) / (full_hz - start_hz), 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    gain = np.power(10.0, (delta_db * smooth) / 20.0)
    y = np.fft.irfft(spectrum * gain, n=x.size)

    source_rms = float(np.sqrt(np.mean(x * x)))
    output_rms = float(np.sqrt(np.mean(y * y)))
    if source_rms > 1e-9 and output_rms > 1e-9:
        y *= source_rms / output_rms
    return y, delta_db


def _apply_energy(samples, scale: float):
    import numpy as np

    y = np.asarray(samples, dtype=np.float64) * float(scale)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.99:
        y *= 0.99 / peak
    return y


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Amora emotion/prosody V2 auditions")
    parser.add_argument("--out-dir", type=Path, default=Path("voice-emotion-wavs"))
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf
    from supertonic import TTS

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tts = TTS(auto_download=True)

    cache_root = Path.home() / ".cache" / "supertonic3" / "voice_styles"
    f1_path = cache_root / "F1.json"
    f5_path = cache_root / "F5.json"
    if not f1_path.is_file() or not f5_path.is_file():
        raise FileNotFoundError(f"voice styles F1/F5 não encontrados em {cache_root}")

    styles_dir = out_dir / "styles"
    mixed_paths = generate_candidates(
        primary_path=f1_path,
        secondary_path=f5_path,
        output_dir=styles_dir,
    )
    mix_60_40 = next((path for path in mixed_paths if "p60" in path.stem and "s40" in path.stem), None)
    if mix_60_40 is None:
        raise RuntimeError("mix 60/40 não foi gerado")
    style = tts.get_voice_style_from_path(mix_60_40)

    planner = VoiceProsodyPlanner()
    report: dict[str, object] = {
        "identity_base": "AMORA_CHILD_FINAL_B_PLUS60",
        "voice_signature": SIGNATURE.SIGNATURE_ID,
        "expression_contract": "AMORA_VOICE_EXPRESSION_V2",
        "rules": {
            "new_formant_or_vtl_shift": False,
            "speaker_or_style_changed_by_emotion": False,
            "signature_texture_changed_by_emotion": False,
            "f0_register_is_bounded_expression": [0.97, 1.02],
            "optional_module_fails_to_neutral": True,
        },
        "python_audition_applies": [
            "rate_scale",
            "pitch_range_scale",
            "f0_register_scale",
            "energy_scale",
            "softness/firmness via bounded RMS-matched tone shelf",
        ],
        "reserved_for_runtime_adapter": ["pause_scale"],
        "presets": [],
        "outputs": 0,
    }

    plans: dict[str, object] = {}
    for preset in PRESETS:
        plan = planner.safe_plan(
            preset["mood"],
            emotion=preset["emotion"],
            intensity=float(preset["intensity"]),
            affect=preset["affect"],
        )
        plans[str(preset["id"])] = plan
        report["presets"].append(
            {
                "id": preset["id"],
                "mood": preset["mood"].model_dump(mode="json"),
                "emotion": preset["emotion"].value if preset["emotion"] is not None else None,
                "intensity": preset["intensity"],
                "plan": plan.model_dump(mode="json"),
            }
        )

    for index, phrase in enumerate(PHRASES, start=1):
        phrase_dir = out_dir / f"phrase_{index:02d}"
        phrase_dir.mkdir(parents=True, exist_ok=True)
        (phrase_dir / "text.txt").write_text(phrase + "\n", encoding="utf-8")

        for preset in PRESETS:
            plan = plans[str(preset["id"])]
            wav, _duration = tts.synthesize(
                phrase,
                voice_style=style,
                lang="pt",
                total_steps=args.steps,
                speed=plan.rate_scale,
            )

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temp_path = Path(handle.name)
            try:
                tts.save_audio(wav, str(temp_path))
                source, sample_rate = sf.read(str(temp_path), dtype="float64", always_2d=False)
            finally:
                temp_path.unlink(missing_ok=True)

            if getattr(source, "ndim", 1) == 2:
                source = np.mean(source, axis=1)
            source = np.asarray(source, dtype=np.float64)

            candidate = _signature_candidate(
                pitch_range_scale=plan.pitch_range_scale,
                f0_register_scale=plan.f0_register_scale,
            )
            processed, _source_f0, _applied_ratio = transform_world(source, int(sample_rate), candidate)
            processed = apply_soft_harmonic_texture(processed)
            processed, tone_delta_db = _apply_expression_tone(
                processed,
                int(sample_rate),
                softness=plan.softness,
                firmness=plan.firmness,
            )
            processed = _apply_energy(processed, plan.energy_scale)

            output_path = phrase_dir / f"emotion_{str(preset['id']).lower()}_v2.wav"
            sf.write(str(output_path), processed, int(sample_rate), subtype="PCM_16")
            report["outputs"] = int(report["outputs"]) + 1
            print(f"{output_path} tone_delta_db={tone_delta_db:+.3f}")

    (out_dir / "emotion-expression-v2-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
