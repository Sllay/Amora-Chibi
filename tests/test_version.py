from amora import __version__
from amora.app import AmoraCore


def test_health_report_uses_package_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    core = AmoraCore.bootstrap()
    try:
        assert core.health_report()["version"] == __version__
    finally:
        core.close()
