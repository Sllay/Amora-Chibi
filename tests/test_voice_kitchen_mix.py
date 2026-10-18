from __future__ import annotations

import importlib.util
import math
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "voice_kitchen" / "mix_supertonic_styles.py"
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_mix", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

mix_styles = MODULE.mix_styles


def _voice(ttl: list, dp: list) -> dict:
    return {
        "style_ttl": {"data": ttl, "shape": [1, 2, 2]},
        "style_dp": {"data": dp, "shape": [1, 1, 2]},
        "meta": {"name": "primary"},
    }


def test_mix_keeps_primary_dp_and_metadata() -> None:
    primary = _voice([[[1.0, 0.0], [0.0, 1.0]]], [[[1.0, 2.0]]])
    secondary = _voice([[[0.0, 1.0], [1.0, 0.0]]], [[[9.0, 9.0]]])

    mixed = mix_styles(primary, secondary, primary_weight=0.70)

    assert mixed["style_dp"] == primary["style_dp"]
    assert mixed["meta"] == primary["meta"]
    assert mixed["amora_voice_kitchen"]["primary_weight"] == 0.7
    assert mixed["amora_voice_kitchen"]["secondary_weight"] == 0.3
    assert mixed["amora_voice_kitchen"]["style_dp_source"] == "primary"


def test_mix_renormalizes_ttl_final_axis() -> None:
    primary = _voice([[[1.0, 0.0], [0.0, 1.0]]], [[[1.0, 2.0]]])
    secondary = _voice([[[0.0, 1.0], [1.0, 0.0]]], [[[9.0, 9.0]]])

    mixed = mix_styles(primary, secondary, primary_weight=0.60)
    rows = mixed["style_ttl"]["data"][0]

    for row in rows:
        assert math.isclose(math.sqrt(sum(v * v for v in row)), 1.0, rel_tol=1e-9)

    expected_first = [0.6 / math.sqrt(0.52), 0.4 / math.sqrt(0.52)]
    assert math.isclose(rows[0][0], expected_first[0], rel_tol=1e-9)
    assert math.isclose(rows[0][1], expected_first[1], rel_tol=1e-9)


def test_mix_rejects_incompatible_ttl_shapes() -> None:
    primary = _voice([[[1.0, 0.0]]], [[[1.0, 2.0]]])
    secondary = _voice([[[0.0, 1.0], [1.0, 0.0]]], [[[9.0, 9.0]]])

    try:
        mix_styles(primary, secondary, primary_weight=0.70)
    except ValueError as exc:
        assert "style_ttl incompatíveis" in str(exc)
    else:
        raise AssertionError("shape incompatível deveria falhar")
