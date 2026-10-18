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


def test_world_candidates_include_reference_and_polished_child_levels() -> None:
    candidates = MODULE.DEFAULT_CANDIDATES
    assert [c.id for c in candidates] == [
        "WORLD_RESYNTH_NEUTRAL",
        "WORLD_CHILD_SOFT",
        "WORLD_CHILD_BALANCED",
        "WORLD_CHILD_CLEAR",
        "WORLD_CHILD_BALANCED_POLISHED",
        "WORLD_CHILD_CLEAR_POLISHED",
        "WORLD_CHILD_PLUS_POLISHED",
    ]

    neutral = candidates[0]
    assert neutral.target_f0_median_hz is None
    assert neutral.max_f0_ratio == 1.0
    assert neutral.pitch_range_scale == 1.0
    assert neutral.high_shelf_db == 0.0
    assert all(ratio == 1.0 for _, ratio in neutral.spectral_ratios)


def test_child_candidates_preserve_low_body_and_taper_highs() -> None:
    for candidate in MODULE.DEFAULT_CANDIDATES[1:]:
        ratios = dict(candidate.spectral_ratios)
        assert ratios[0.0] == 1.0
        assert ratios[250.0] == 1.0
        assert ratios[700.0] > ratios[3000.0] >= ratios[8000.0]
        assert max(ratios.values()) <= 1.13
        assert candidate.aperiodicity_scale == 1.0


def test_polished_candidates_are_only_slightly_more_child_and_less_bright() -> None:
    polished = MODULE.DEFAULT_CANDIDATES[4:]
    assert [c.target_f0_median_hz for c in polished] == [268.0, 280.0, 288.0]
    assert max(c.max_f0_ratio for c in polished) <= 1.19
    assert max(c.pitch_range_scale for c in polished) <= 1.05
    assert [c.high_shelf_db for c in polished] == [-1.5, -2.0, -2.6]

    # The strongest candidate gets more child-size cue in low/mids while the
    # top octave returns to neutral, avoiding the previous strident/ET tendency.
    plus = polished[-1]
    ratios = dict(plus.spectral_ratios)
    assert ratios[700.0] == 1.13
    assert ratios[1500.0] == 1.115
    assert ratios[8000.0] == 1.0


def test_piecewise_anchor_mapping_is_monotonic_and_not_uniform() -> None:
    candidate = MODULE.DEFAULT_CANDIDATES[-1]
    source_anchor = [hz for hz, _ in candidate.spectral_ratios]
    target_anchor = [hz * ratio for hz, ratio in candidate.spectral_ratios]

    assert all(b > a for a, b in zip(source_anchor, source_anchor[1:]))
    assert all(b > a for a, b in zip(target_anchor, target_anchor[1:]))

    ratios = [ratio for _, ratio in candidate.spectral_ratios]
    assert len(set(ratios)) > 3


def test_ratio_to_semitones_identity_and_bound() -> None:
    assert MODULE.ratio_to_semitones(1.0) == 0.0
    assert 3.0 < MODULE.ratio_to_semitones(1.19) < 3.1
