#!/usr/bin/env python3
"""Render bounded emotion/prosody auditions on top of Amora VoiceSignature V1.

The frozen identity is never replaced here:
- 60/40 Supertonic style remains the source identity;
- FINAL_B morphology remains fixed;
- VoiceSignature V1 texture remains fixed.

Only provider-independent expression dimensions are auditioned: source speech
rate, F0 contour range around the same median, and bounded output energy. No
absolute pitch shift or new formant/VTL transform is introduced.
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
        "intensity": 0.65,
        "affect": None,
    },
    {
        "id": "CARINHOSA",
        "mood": _mood(0.55, 0.16, 0.0),
        "emotion": EmotionType.GRATITUDE,
        "intensity": 0.35,
        "affect": _affect(affection=0.85, gratitude=0.45, trust=0.80),
    },
    {
        "id": "BRAVA",
        "mood": _mood(-0.65, 0.78, 0.55),
        "emotion": EmotionType.ANGER,
        "intensity": 0.80,
        "affect": _affect(anger=0.75, irritation=0.60, trust=0.35),
    },
    {
        "id": "TRISTE",
        "mood": _mood(-0.65, 0.10, -0.30),
        "emotion": EmotionType.DISAPPOINTMENT,
        "intensity": 0.80,
        "affect": None,
    },
)


def _signature_candidate(pitch_range_scale: float) -> WorldCandidate:
    # Keep FINAL_B identity and Signature V1 texture fixed. Only F0 contour range
    # changes around the same target median.
    return WorldCandidate(
        f"AMORA_EXPRESSION_RANGE_{pitch_range_scale:.4f}",
        FINAL_B.target_f0_median_hz,
        FINAL_B.max_f0_ratio,
        FINAL_B.pitch_range_scale * pitch_range_scale,
        FINAL_B.spectral_ratios,
        SIGNATURE.APERIODICITY_SCALE,
        FINAL_B.high_shelf_db,
    )


def _apply_energy(samples, scale: float):
    import numpy as np

    y = np.asarray(samples, dtype=np.float64) * float(scale)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.99:
        y *= 0.99 / peak
    return y


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Amora emotion/prosody auditions")
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
        "expression_contract": "AMORA_VOICE_EXPRESSION_V1",
        "rules": {
            "absolute_pitch_shift": False,
            "new_formant_or_vtl_shift": False,
            "signature_parameters_changed_by_emotion": False,
            "optional_module_fails_to_neutral": True,
        },
        "python_audition_applies": ["rate_scale", "pitch_range_scale", "energy_scale"],
        "reserved_for_runtime_adapter": ["pause_scale", "softness", "firmness"],
        "presets": [],
        "outputs": 0,
    }

    for preset in PRESETS:
        plan = planner.safe_plan(
            preset["mood"],
            emotion=preset["emotion"],
            intensity=float(preset["intensity"]),
            affect=preset["affect"],
        )
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
            plan = planner.safe_plan(
                preset["mood"],
                emotion=preset["emotion"],
                intensity=float(preset["intensity"]),
                affect=preset["affect"],
            )

            wav, _duration = tts.synthesize(
                phrase,
                voice_style=style,
                lang="pt",
                total_steps=args.steps,
                speed=plan.rate_scale,
            )

            # save_audio is the public Supertonic path and guarantees the correct
            # model sample rate without guessing an undocumented runtime field.
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

            candidate = _signature_candidate(plan.pitch_range_scale)
            processed, _source_f0, _applied_ratio = transform_world(source, int(sample_rate), candidate)
            processed = apply_soft_harmonic_texture(processed)
            processed = _apply_energy(processed, plan.energy_scale)

            output_path = phrase_dir / f"emotion_{str(preset['id']).lower()}.wav"
            sf.write(str(output_path), processed, int(sample_rate), subtype="PCM_16")
            report["outputs"] = int(report["outputs"]) + 1
            print(output_path)

    (out_dir / "emotion-expression-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
