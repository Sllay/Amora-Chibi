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


def test_pairing_redeem_is_single_use_across_instances(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from amora.pairing import PairingManager
    from amora.time import SimulatedClock
    clock=SimulatedClock()
    first=PairingManager(tmp_path/'pairing.json',clock)
    second=PairingManager(tmp_path/'pairing.json',clock)
    code=first.issue().code
    barrier=threading.Barrier(2)
    def redeem(manager):
        barrier.wait()
        return manager.redeem(code)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(redeem,[first,second]))
    assert sum(results)==1


def test_cli_pair_never_bootstraps_core_and_redeems_on_running_core(tmp_path, monkeypatch, capsys):
    import re
    import sys
    from amora.app import AmoraCore
    from amora.__main__ import main
    from amora.api import create_api
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    core = AmoraCore.bootstrap()
    def forbidden(*args, **kwargs):
        raise AssertionError("pair must not bootstrap Core or open storage")
    monkeypatch.setattr(AmoraCore, "bootstrap", forbidden)
    monkeypatch.setattr("amora.storage.AmoraDatabase.__init__", forbidden)
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setattr(sys, "argv", ["amora", "pair"])
    try:
        assert main() == 0
        output = capsys.readouterr().out
        code = re.search(r"Código de pareamento: (\d{6})", output).group(1)
        assert "Uso único; máximo de 5 tentativas." in output
        assert (tmp_path / "cache/pairing.json").stat().st_mode & 0o777 == 0o600
        with TestClient(create_api(core), base_url="https://amora.example") as client:
            assert client.post("/v1/pairing/redeem", json={"code": code}).status_code == 200
            assert client.post("/v1/pairing/redeem", json={"code": code}).status_code == 401
    finally:
        core.close()
