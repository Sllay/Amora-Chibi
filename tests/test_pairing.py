from datetime import datetime, timedelta, timezone

from amora.pairing import PairingManager
from amora.time import SimulatedClock


def test_pairing_code_is_single_use_and_cache_never_contains_clear_code(tmp_path) -> None:
    clock = SimulatedClock(datetime(2026, 10, 8, tzinfo=timezone.utc))
    path = tmp_path / "pairing.json"
    manager = PairingManager(path, clock)
    issued = manager.issue()

    assert len(issued.code) == 6 and issued.code.isdigit()
    assert issued.code not in path.read_text(encoding="utf-8")
    assert manager.redeem(issued.code) is True
    assert path.exists() is False
    assert manager.redeem(issued.code) is False


def test_pairing_expires_and_limits_attempts(tmp_path) -> None:
    clock = SimulatedClock(datetime(2026, 10, 8, tzinfo=timezone.utc))
    path = tmp_path / "pairing.json"
    manager = PairingManager(path, clock, ttl_seconds=10, max_attempts=2)
    issued = manager.issue()
    assert manager.redeem("000000") is False
    assert manager.redeem("000001") is False
    assert path.exists() is False

    issued = manager.issue()
    clock.advance(timedelta(seconds=11))
    assert manager.redeem(issued.code) is False
    assert path.exists() is False
