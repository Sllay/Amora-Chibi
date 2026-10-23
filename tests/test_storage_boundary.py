from amora.app import AmoraCore
from amora.storage.backend import SQLiteBackend


class RecordingBackend(SQLiteBackend):
    def __init__(self):
        self.paths = []

    def connect(self, path):
        self.paths.append(path)
        return super().connect(path)


def test_all_domain_stores_use_injected_backend(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path))
    backend = RecordingBackend()
    core = AmoraCore.bootstrap(storage=backend)
    try:
        assert len(backend.paths) == 9
        assert set(backend.paths) == {tmp_path / 'data/amora.db'}
        assert core.health_report()['status'] == 'ok'
        snapshot = core.create_backup('boundary.db')
        assert snapshot.is_file()
        assert core.avatar.store.asset_backup_path(snapshot).is_file()
    finally:
        core.close()
