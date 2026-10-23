from amora.app import AmoraCore
from amora.storage.backend import SQLiteBackend


class RecordingBackend(SQLiteBackend):
    def __init__(self):
        self.paths = []

    def connect(self, path):
        self.paths.append(path)
        return super().connect(path)


def test_all_domain_stores_use_injected_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    backend = RecordingBackend()
    core = AmoraCore.bootstrap(storage=backend)
    try:
        assert len(backend.paths) == 9
        assert set(backend.paths) == {tmp_path / "data/amora.db"}
        assert core.health_report()["status"] == "ok"
        snapshot = core.create_backup("boundary.db")
        assert snapshot.is_file()
        assert core.avatar.store.asset_backup_path(snapshot).is_file()
    finally:
        core.close()


def test_bootstrap_failure_releases_all_opened_connections(tmp_path, monkeypatch):
    import sqlite3
    import pytest

    class FailingBackend(SQLiteBackend):
        def __init__(self):
            self.connections = []

        def connect(self, path):
            if len(self.connections) == 2:
                raise RuntimeError("injected initialization failure")
            connection = super().connect(path)
            self.connections.append(connection)
            return connection

    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    backend = FailingBackend()
    with pytest.raises(RuntimeError, match="initialization failure"):
        AmoraCore.bootstrap(storage=backend)
    for connection in backend.connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
