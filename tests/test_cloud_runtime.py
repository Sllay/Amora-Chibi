from fastapi.testclient import TestClient
import pytest
from amora.app import AmoraCore
from amora.api import create_api
from amora.settings import ServerSettings


def test_cloud_refuses_ephemeral_sqlite_and_missing_token(monkeypatch):
    monkeypatch.setenv('AMORA_MODE', 'cloud')
    monkeypatch.delenv('AMORA_SECRET_LOCAL_API_TOKEN', raising=False)
    with pytest.raises(ValueError, match='injected'):
        AmoraCore.bootstrap()
    monkeypatch.setenv('AMORA_SECRET_LOCAL_API_TOKEN', 'test-only-service-token')
    with pytest.raises(ValueError, match='durable volume'):
        AmoraCore.bootstrap()


def test_environment_host_port(monkeypatch):
    monkeypatch.setenv('AMORA_MODE', 'cloud')
    monkeypatch.setenv('PORT', '8080')
    assert ServerSettings.from_environment().host == '0.0.0.0'
    assert ServerSettings.from_environment().port == 8080
    monkeypatch.setenv('PORT', '0')
    with pytest.raises(ValueError):
        ServerSettings.from_environment()


def test_readiness_does_not_depend_on_voice_and_shutdown_closes_core(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path))
    core = AmoraCore.bootstrap()
    app = create_api(core, own_core=True)
    with TestClient(app) as client:
        assert client.get('/ready').json()['status'] == 'ready'
        assert client.get('/live').status_code == 200
        assert client.get('/health').json()['database_integrity'] == 'ok'
        assert client.get('/v1/state').status_code == 401
        assert client.get('/web/').status_code == 200
        assert client.get('/avatar/').status_code == 200
        monkeypatch.setattr(core.database, 'integrity_check', lambda: 'corrupt')
        assert client.get('/health').status_code == 503
    assert core._closed
    assert core.readiness()['status'] == 'not_ready'
    core.close()  # idempotent


def test_readiness_fails_on_database_outage(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path))
    core = AmoraCore.bootstrap()
    try:
        def fail():
            raise RuntimeError('credential-never-echo')
        monkeypatch.setattr(core.database, 'schema_version', fail)
        with TestClient(create_api(core)) as client:
            response = client.get('/ready')
            assert response.status_code == 503
            assert 'credential-never-echo' not in response.text
    finally:
        core.close()
