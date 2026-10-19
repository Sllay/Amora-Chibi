from __future__ import annotations

import importlib.util
import math
import struct
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "android" / "tools" / "build_amora_voice_bin.py"
SPEC = importlib.util.spec_from_file_location("amora_android_voice_bin", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_fixture(path: Path) -> None:
    # TTL shape [5, 2, 2], DP shape [5, 1, 2]. Speaker 0 and 4 are deliberately distinct.
    ttl_shape = (5, 2, 2)
    dp_shape = (5, 1, 2)
    ttl = []
    dp = []
    for speaker in range(5):
        ttl.extend([1.0 + speaker, 2.0 + speaker, 3.0 + speaker, 4.0 + speaker])
        dp.extend([10.0 + speaker, 20.0 + speaker])
    payload = struct.pack("<6q", *(ttl_shape + dp_shape))
    payload += struct.pack(f"<{len(ttl)}f", *ttl)
    payload += struct.pack(f"<{len(dp)}f", *dp)
    path.write_bytes(payload)


def test_build_amora_voice_bin_keeps_primary_dp_and_single_speaker(tmp_path: Path) -> None:
    source = tmp_path / "voice.bin"
    output = tmp_path / "amora_voice.bin"
    _write_fixture(source)

    report = MODULE.build_amora_voice_bin(source, output)
    ttl_shape, ttl, dp_shape, dp = MODULE.parse_voice_bin(output)

    assert ttl_shape == (1, 2, 2)
    assert dp_shape == (1, 1, 2)
    assert dp == [10.0, 20.0]
    assert report["primary_speaker"] == 0
    assert report["secondary_speaker"] == 4
    assert report["primary_weight"] == 0.60

    # Every final-axis TTL vector is normalized exactly like Voice Kitchen.
    for row in range(2):
        a, b = ttl[row * 2:row * 2 + 2]
        assert math.isclose(math.sqrt(a * a + b * b), 1.0, rel_tol=1e-6)


def test_build_amora_voice_bin_rejects_invalid_speaker(tmp_path: Path) -> None:
    source = tmp_path / "voice.bin"
    _write_fixture(source)
    try:
        MODULE.build_amora_voice_bin(source, tmp_path / "out.bin", secondary_speaker=8)
    except ValueError as error:
        assert "speaker 8" in str(error)
    else:
        raise AssertionError("invalid speaker should fail")
