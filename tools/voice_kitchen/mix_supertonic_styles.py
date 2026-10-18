#!/usr/bin/env python3
"""Create deterministic Supertonic voice-style blends for Amora Voice Kitchen.

The tool intentionally changes only ``style_ttl`` (timbre/identity) by default
and keeps ``style_dp`` (duration/prosody) from the primary voice. This lets us
compare timbre blends without moving two variables at once.

It accepts the standard Supertonic JSON shape where ``style_ttl`` and
``style_dp`` are objects containing a ``data`` tensor, while also accepting a
bare nested-list tensor for small fixtures/tests.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_PRIMARY_WEIGHTS = (0.70, 0.60)


def _tensor_data(document: dict[str, Any], key: str) -> list[Any]:
    value = document.get(key)
    if isinstance(value, dict):
        data = value.get("data")
    else:
        data = value
    if not isinstance(data, list):
        raise ValueError(f"{key} precisa conter um tensor JSON em 'data'")
    return data


def _shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    if not value:
        return (0,)
    child_shapes = {_shape(item) for item in value}
    if len(child_shapes) != 1:
        raise ValueError("tensor irregular/ragged não é suportado")
    return (len(value),) + next(iter(child_shapes))


def _mix_nested(primary: Any, secondary: Any, primary_weight: float) -> Any:
    if isinstance(primary, list) and isinstance(secondary, list):
        if len(primary) != len(secondary):
            raise ValueError("tensores com comprimentos diferentes")
        return [
            _mix_nested(a, b, primary_weight)
            for a, b in zip(primary, secondary, strict=True)
        ]
    if isinstance(primary, (int, float)) and isinstance(secondary, (int, float)):
        secondary_weight = 1.0 - primary_weight
        return float(primary) * primary_weight + float(secondary) * secondary_weight
    raise ValueError("tensor contém valor não numérico ou estrutura incompatível")


def _normalize_last_axis(value: Any, epsilon: float = 1e-12) -> Any:
    """Normalize each final-axis vector to unit L2 norm.

    Supertonic style_ttl preset rows live on/near the unit sphere. Convex
    interpolation shrinks their norms, so the Voice Kitchen renormalizes each
    final-axis vector after mixing. If a vector is effectively zero, it is left
    untouched instead of introducing NaNs.
    """

    if not isinstance(value, list) or not value:
        return value
    if all(isinstance(item, (int, float)) for item in value):
        norm = math.sqrt(sum(float(item) ** 2 for item in value))
        if norm <= epsilon:
            return [float(item) for item in value]
        return [float(item) / norm for item in value]
    return [_normalize_last_axis(item, epsilon=epsilon) for item in value]


def mix_styles(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    primary_weight: float,
    normalize_ttl: bool = True,
    keep_primary_dp: bool = True,
) -> dict[str, Any]:
    if not 0.0 <= primary_weight <= 1.0:
        raise ValueError("primary_weight precisa estar entre 0 e 1")

    primary_ttl = _tensor_data(primary, "style_ttl")
    secondary_ttl = _tensor_data(secondary, "style_ttl")
    if _shape(primary_ttl) != _shape(secondary_ttl):
        raise ValueError(
            f"style_ttl incompatíveis: {_shape(primary_ttl)} != {_shape(secondary_ttl)}"
        )

    mixed_ttl = _mix_nested(primary_ttl, secondary_ttl, primary_weight)
    if normalize_ttl:
        mixed_ttl = _normalize_last_axis(mixed_ttl)

    output = copy.deepcopy(primary)
    if isinstance(output.get("style_ttl"), dict):
        output["style_ttl"]["data"] = mixed_ttl
    else:
        output["style_ttl"] = mixed_ttl

    if not keep_primary_dp:
        primary_dp = _tensor_data(primary, "style_dp")
        secondary_dp = _tensor_data(secondary, "style_dp")
        if _shape(primary_dp) != _shape(secondary_dp):
            raise ValueError(
                f"style_dp incompatíveis: {_shape(primary_dp)} != {_shape(secondary_dp)}"
            )
        mixed_dp = _mix_nested(primary_dp, secondary_dp, primary_weight)
        if isinstance(output.get("style_dp"), dict):
            output["style_dp"]["data"] = mixed_dp
        else:
            output["style_dp"] = mixed_dp

    output["amora_voice_kitchen"] = {
        "primary_weight": round(primary_weight, 6),
        "secondary_weight": round(1.0 - primary_weight, 6),
        "ttl_normalized": normalize_ttl,
        "style_dp_source": "primary" if keep_primary_dp else "mixed",
    }
    return output


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: raiz JSON precisa ser um objeto")
    _tensor_data(data, "style_ttl")
    _tensor_data(data, "style_dp")
    return data


def _ratio_label(primary_weight: float) -> str:
    primary_pct = round(primary_weight * 100)
    secondary_pct = 100 - primary_pct
    return f"p{primary_pct:02d}_s{secondary_pct:02d}"


def generate_candidates(
    primary_path: Path,
    secondary_path: Path,
    output_dir: Path,
    primary_weights: tuple[float, ...] = DEFAULT_PRIMARY_WEIGHTS,
) -> list[Path]:
    primary = _load(primary_path)
    secondary = _load(secondary_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for primary_weight in primary_weights:
        mixed = mix_styles(
            primary,
            secondary,
            primary_weight=primary_weight,
            normalize_ttl=True,
            keep_primary_dp=True,
        )
        output = output_dir / f"amora_mix_{_ratio_label(primary_weight)}.json"
        output.write_text(
            json.dumps(mixed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(output)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mistura dois voice styles Supertonic mantendo a prosódia da voz principal."
    )
    parser.add_argument("--primary", required=True, type=Path, help="JSON da voz principal (speaker 0/F1 no nosso experimento)")
    parser.add_argument("--secondary", required=True, type=Path, help="JSON da voz secundária (speaker 4/F5 no nosso experimento)")
    parser.add_argument("--out-dir", type=Path, default=Path("voice-kitchen-out"))
    parser.add_argument(
        "--primary-weight",
        type=float,
        action="append",
        dest="weights",
        help="Peso da voz principal; pode repetir. Padrão: 0.70 e 0.60.",
    )
    args = parser.parse_args()

    weights = tuple(args.weights) if args.weights else DEFAULT_PRIMARY_WEIGHTS
    for weight in weights:
        if not 0.0 <= weight <= 1.0:
            parser.error("--primary-weight precisa estar entre 0 e 1")

    outputs = generate_candidates(args.primary, args.secondary, args.out_dir, weights)
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
