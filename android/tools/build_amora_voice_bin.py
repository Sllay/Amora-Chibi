#!/usr/bin/env python3
"""Build Amora's frozen 60/40 Supertonic voice.bin for Android.

sherpa-onnx Supertonic voice.bin is:
  6 little-endian int64 dimensions
  ttl float32 payload
  dp float32 payload

The first tensor axis is speaker. Amora's approved laboratory base used the
Voice Kitchen 60% primary / 40% secondary TTL blend while keeping DP from the
primary style. This tool reproduces that operation directly on sherpa's binary
style pack so Android can synthesize the same base family without runtime style
mixing.

The historical F1/F5 -> sherpa speaker 0/4 correspondence remains a physical
A/B gate, not a claim of canonical equivalence. The output is therefore an
Android candidate until Galaxy A15 validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from array import array
from pathlib import Path

HEADER = struct.Struct("<6q")
FLOAT_SIZE = 4


def _product(shape: tuple[int, ...]) -> int:
    out = 1
    for value in shape:
        if value <= 0:
            raise ValueError(f"invalid tensor dimension: {shape}")
        out *= value
    return out


def _read_float32_le(raw: bytes, count: int) -> list[float]:
    if len(raw) != count * FLOAT_SIZE:
        raise ValueError("float payload length mismatch")
    values = array("f")
    values.frombytes(raw)
    if struct.pack("=I", 1) != struct.pack("<I", 1):
        values.byteswap()
    return [float(value) for value in values]


def _write_float32_le(values: list[float]) -> bytes:
    packed = array("f", (float(value) for value in values))
    if struct.pack("=I", 1) != struct.pack("<I", 1):
        packed.byteswap()
    return packed.tobytes()


def parse_voice_bin(path: Path) -> tuple[tuple[int, int, int], list[float], tuple[int, int, int], list[float]]:
    raw = path.read_bytes()
    if len(raw) < HEADER.size:
        raise ValueError("voice.bin smaller than sherpa Supertonic header")
    dims = HEADER.unpack_from(raw, 0)
    ttl_shape = (int(dims[0]), int(dims[1]), int(dims[2]))
    dp_shape = (int(dims[3]), int(dims[4]), int(dims[5]))
    ttl_count = _product(ttl_shape)
    dp_count = _product(dp_shape)
    expected = HEADER.size + (ttl_count + dp_count) * FLOAT_SIZE
    if len(raw) != expected:
        raise ValueError(f"voice.bin size mismatch: got={len(raw)} expected={expected}")
    ttl_start = HEADER.size
    dp_start = ttl_start + ttl_count * FLOAT_SIZE
    ttl = _read_float32_le(raw[ttl_start:dp_start], ttl_count)
    dp = _read_float32_le(raw[dp_start:], dp_count)
    return ttl_shape, ttl, dp_shape, dp


def _speaker_slice(values: list[float], shape: tuple[int, int, int], speaker: int) -> list[float]:
    speakers, rows, width = shape
    if not 0 <= speaker < speakers:
        raise ValueError(f"speaker {speaker} outside 0..{speakers - 1}")
    stride = rows * width
    start = speaker * stride
    return values[start:start + stride]


def _mix_and_normalize_rows(primary: list[float], secondary: list[float], *, rows: int, width: int, primary_weight: float) -> list[float]:
    if len(primary) != rows * width or len(secondary) != rows * width:
        raise ValueError("speaker slice shape mismatch")
    secondary_weight = 1.0 - primary_weight
    mixed = [a * primary_weight + b * secondary_weight for a, b in zip(primary, secondary, strict=True)]
    # Same Voice Kitchen policy: unit-L2 normalize each final-axis TTL vector.
    for row in range(rows):
        start = row * width
        end = start + width
        norm = math.sqrt(sum(value * value for value in mixed[start:end]))
        if norm > 1e-12:
            inv = 1.0 / norm
            for index in range(start, end):
                mixed[index] *= inv
    return mixed


def build_amora_voice_bin(
    source: Path,
    output: Path,
    *,
    primary_speaker: int = 0,
    secondary_speaker: int = 4,
    primary_weight: float = 0.60,
) -> dict[str, object]:
    if not 0.0 <= primary_weight <= 1.0:
        raise ValueError("primary_weight must be in [0, 1]")
    ttl_shape, ttl, dp_shape, dp = parse_voice_bin(source)
    if ttl_shape[0] != dp_shape[0]:
        raise ValueError("TTL/DP speaker counts differ")

    primary_ttl = _speaker_slice(ttl, ttl_shape, primary_speaker)
    secondary_ttl = _speaker_slice(ttl, ttl_shape, secondary_speaker)
    primary_dp = _speaker_slice(dp, dp_shape, primary_speaker)
    mixed_ttl = _mix_and_normalize_rows(
        primary_ttl,
        secondary_ttl,
        rows=ttl_shape[1],
        width=ttl_shape[2],
        primary_weight=primary_weight,
    )

    out_ttl_shape = (1, ttl_shape[1], ttl_shape[2])
    out_dp_shape = (1, dp_shape[1], dp_shape[2])
    payload = HEADER.pack(*(out_ttl_shape + out_dp_shape)) + _write_float32_le(mixed_ttl) + _write_float32_le(primary_dp)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()
    return {
        "source": str(source),
        "output": str(output),
        "source_speakers": ttl_shape[0],
        "primary_speaker": primary_speaker,
        "secondary_speaker": secondary_speaker,
        "primary_weight": primary_weight,
        "secondary_weight": 1.0 - primary_weight,
        "style_ttl_policy": "convex mix + final-axis unit L2 normalization",
        "style_dp_policy": "primary speaker unchanged",
        "output_ttl_shape": list(out_ttl_shape),
        "output_dp_shape": list(out_dp_shape),
        "sha256": digest,
        "bytes": len(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build frozen Amora Supertonic Android voice.bin")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--primary-speaker", type=int, default=0)
    parser.add_argument("--secondary-speaker", type=int, default=4)
    parser.add_argument("--primary-weight", type=float, default=0.60)
    args = parser.parse_args()
    report = build_amora_voice_bin(
        args.source,
        args.output,
        primary_speaker=args.primary_speaker,
        secondary_speaker=args.secondary_speaker,
        primary_weight=args.primary_weight,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
