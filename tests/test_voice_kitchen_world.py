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


def test_world_candidates_include_neutral_and_three_child_levels() -> None:
    candidates = MODULE.DEFAULT_CANDIDATES
    assert [c.id for c in candidates] == [
        "WORLD_RESYNTH_NEUTRAL",
        "WORLD_CHILD_SOFT",
        "WORLD_CHILD_BALANCED",
        "WORLD_CHILD_CLEAR",
    ]

    neutral = candidates[0]
    assert neutral.target_f0_median_hz is None
    assert neutral.max_f0_ratio == 1.0
    assert neutral.pitch_range_scale == 1.0
    assert all(ratio == 1.0 for _, ratio in neutral.spectral_ratios)


def test_child_candidates_preserve_low_body_and_taper_highs() -> None:
    for candidate in MODULE.DEFAULT_CANDIDATES[1:]:
        ratios = dict(candidate.spectral_ratios)
        assert ratios[0.0] == 1.0
        assert ratios[250.0] == 1.0
        assert ratios[700.0] > ratios[3000.0] > ratios[8000.0] >= 1.0
        assert max(ratios.values()) <= 1.12
        assert candidate.aperiodicity_scale == 1.0


def test_f0_targets_are_bounded_and_contour_range_is_small() -> None:
    child = MODULE.DEFAULT_CANDIDATES[1:]
    assert [c.target_f0_median_hz for c in child] == [245.0, 260.0, 275.0]
    assert max(c.max_f0_ratio for c in child) <= 1.18
    assert max(c.pitch_range_scale for c in child) <= 1.06


def test_piecewise_map_is_monotonic_and_not_uniform() -> None:
    import numpy as np

    candidate = MODULE.DEFAULT_CANDIDATES[2]
    mapped = MODULE._piecewise_source_frequency_map(
        sample_rate=24000,
        bins=513,
        spectral_ratios=candidate.spectral_ratios,
    )
    assert np.all(np.diff(mapped) > 0.0)
    assert mapped[0] == 0.0
    assert mapped[-1] == 12000.0

    # The mapping changes slope by band; this guards against accidentally
    # returning to the rejected global/uniform formant shift.
    slopes = np.diff(mapped)
    assert float(np.max(slopes) - np.min(slopes)) > 0.01


def test_ratio_to_semitones_identity_and_bound() -> None:
    assert MODULE.ratio_to_semitones(1.0) == 0.0
    assert 2.7 < MODULE.ratio_to_semitones(1.18) < 2.9
