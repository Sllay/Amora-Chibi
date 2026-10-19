from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "voice_kitchen"
    / "apply_amora_signature_v1.py"
)
SPEC = importlib.util.spec_from_file_location("amora_voice_signature_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_signature_preserves_frozen_final_b_age_identity() -> None:
    base = MODULE.FINAL_B
    signature = MODULE.SIGNATURE_CANDIDATE

    assert base.id == "AMORA_CHILD_FINAL_B_PLUS60"
    assert signature.id == "AMORA_VOICE_SIGNATURE_V1_SOFT"
    assert signature.target_f0_median_hz == base.target_f0_median_hz
    assert signature.max_f0_ratio == base.max_f0_ratio
    assert signature.pitch_range_scale == base.pitch_range_scale
    assert signature.spectral_ratios == base.spectral_ratios
    assert signature.high_shelf_db == base.high_shelf_db


def test_signature_texture_is_intentionally_subtle() -> None:
    assert 0.95 <= MODULE.APERIODICITY_SCALE < 1.0
    assert 1.0 < MODULE.HARMONIC_DRIVE <= 1.35
    assert 0.0 < MODULE.HARMONIC_WET <= 0.06


def test_signature_does_not_reintroduce_rejected_robotic_chain() -> None:
    # The canonical candidate must not depend on the earlier pitch/bitcrush/
    # sample-hold/ring-modulation path that BK rejected as chipmunk/alien.
    signature = MODULE.SIGNATURE_CANDIDATE
    assert signature.target_f0_median_hz == 284.8
    assert signature.aperiodicity_scale == MODULE.APERIODICITY_SCALE
    assert MODULE.HARMONIC_WET == 0.05
