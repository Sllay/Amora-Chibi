from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "voice_kitchen"
    / "apply_world_vtl_candidates.py"
)
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_world", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_world_candidates_are_conservative_and_include_neutral_resynthesis() -> None:
    candidates = MODULE.DEFAULT_CANDIDATES
    assert len(candidates) == 4
    assert candidates[0].id == "WORLD_RESYNTH_NEUTRAL"
    assert candidates[0].pitch_semitones == 0.0
    assert candidates[0].tract_ratio == 1.0
    assert candidates[0].aperiodicity_scale == 1.0

    assert max(abs(c.pitch_semitones) for c in candidates) <= 0.35
    assert max(c.tract_ratio for c in candidates) <= 1.055
    assert min(c.aperiodicity_scale for c in candidates) >= 0.96


def test_tract_ratio_is_far_smaller_than_rejected_uniform_formant_shift() -> None:
    # 1.055x is below +1 semitone equivalent, deliberately smaller than the
    # previous Signalsmith audition whose minimum uniform formant shift was +1.5 st.
    maximum_equivalent = max(
        MODULE.tract_ratio_to_semitones(c.tract_ratio) for c in MODULE.DEFAULT_CANDIDATES
    )
    assert 0.9 < maximum_equivalent < 1.0


def test_semitone_ratio_identity_and_small_shift() -> None:
    assert MODULE.semitone_ratio(0.0) == 1.0
    assert 1.01 < MODULE.semitone_ratio(0.35) < 1.03
