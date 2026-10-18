from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "voice_kitchen"
    / "apply_world_final_ab.py"
)
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_final_ab", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_final_ab_contains_only_two_close_candidates() -> None:
    candidates = MODULE.FINAL_CANDIDATES
    assert [c.id for c in candidates] == [
        "AMORA_CHILD_FINAL_A_CLEAR60",
        "AMORA_CHILD_FINAL_B_PLUS60",
    ]
    assert abs(candidates[0].target_f0_median_hz - candidates[1].target_f0_median_hz) <= 2.0
    assert abs(candidates[0].high_shelf_db - candidates[1].high_shelf_db) <= 0.2


def test_final_a_leans_clear_and_final_b_leans_plus() -> None:
    a, b = MODULE.FINAL_CANDIDATES

    # A keeps slightly more clarity: lower F0/VTL push and less HF attenuation.
    assert a.target_f0_median_hz < b.target_f0_median_hz
    assert a.max_f0_ratio < b.max_f0_ratio
    assert a.high_shelf_db > b.high_shelf_db

    ratios_a = dict(a.spectral_ratios)
    ratios_b = dict(b.spectral_ratios)
    assert ratios_a[700.0] < ratios_b[700.0]
    assert ratios_a[1500.0] < ratios_b[1500.0]
    assert ratios_a[3000.0] < ratios_b[3000.0]


def test_final_candidates_stay_inside_clear_plus_bounds() -> None:
    a, b = MODULE.FINAL_CANDIDATES
    for candidate in (a, b):
        assert 280.0 < candidate.target_f0_median_hz < 288.0
        assert 1.18 < candidate.max_f0_ratio < 1.19
        assert 1.045 < candidate.pitch_range_scale < 1.05
        assert -2.6 < candidate.high_shelf_db < -2.0
        ratios = dict(candidate.spectral_ratios)
        assert ratios[250.0] == 1.0
        assert 1.12 < ratios[700.0] < 1.13
        assert 1.105 < ratios[1500.0] < 1.115
        assert ratios[8000.0] == 1.0
