from pathlib import Path
from amora.app import AmoraCore


def test_bootstrap_survives_restart_with_same_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); first = AmoraCore.bootstrap()
    try:
        assert first.identities.resolve_alias("Bad King").id == "PERSON_BK"; token = first.secrets.get("LOCAL_API_TOKEN"); assert token
    finally: first.close()
    second = AmoraCore.bootstrap()
    try:
        assert second.identities.resolve_alias("Slay").id == "PERSON_BK"
        assert second.secrets.get("LOCAL_API_TOKEN") == token
        report = second.health_report()
        assert report["schema_version"] == 3
        assert report["memory_count"] == 0
    finally: second.close()
