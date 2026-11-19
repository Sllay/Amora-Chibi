"""Staging-only Aiven /health + /ready integrity gates.

Fake connection fixtures exercise state and policy; no Aiven credentials,
external socket or production data are present.
"""
from __future__ import annotations

from types import SimpleNamespace
from threading import RLock

import pytest

from amora.app import AmoraCore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_connection import PostgresTargetPolicy


class FakePgConnection:
    def __init__(self, target):
        self._target = target
        self._raw = object()
        self.verified = []

    def verify_prepared_schema(self, schema):
        self.verified.append(schema)


def _db():
    target = PostgresTargetPolicy(
        host="pg-fictional.aivencloud.com", port=28450,
        database="defaultdb", require_tls=True,
    )
    db = object.__new__(AmoraDatabase)
    db._lock = RLock()
    db.expected_schema_version = 18
    db.backend = SimpleNamespace(capabilities=SimpleNamespace(name="postgres-candidate"))
    db._conn = FakePgConnection(target)
    return db


def test_stage_health_requires_operator_gate_and_structural_pass(monkeypatch):
    db = _db()
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres-staging")
    monkeypatch.delenv("AMORA_PG_STAGING_APPROVAL", raising=False)
    assert db.integrity_check() == "postgres-candidate-unverified"

    monkeypatch.setenv(
        "AMORA_PG_STAGING_APPROVAL", "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE",
    )
    called = []

    def verify(raw, target, **kwargs):
        called.append((raw, target, kwargs))
        return SimpleNamespace(status="structural_pass")

    monkeypatch.setattr(
        "amora.storage.postgres_run3_preflight.inspect_prepared_postgres", verify,
    )
    assert db.integrity_check() == "ok"
    assert len(called) == 1
    assert called[0][0] is db._conn._raw
    assert called[0][1] == db._conn._target
    assert called[0][2]["require_memory_epoch"] is True
    assert called[0][2]["allow_empty_migrations_for_import"] is False
    assert db._conn.verified == [18, 18]

    monkeypatch.setattr(
        "amora.storage.postgres_run3_preflight.inspect_prepared_postgres",
        lambda *_args, **_kwargs: SimpleNamespace(status="blocked"),
    )
    assert db.integrity_check() == "postgres-candidate-unverified"
    db._conn._target = PostgresTargetPolicy()
    assert db.integrity_check() == "postgres-candidate-unverified"
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "libsql")
    assert db.integrity_check() == "postgres-candidate-unverified"


def _core(status):
    core = object.__new__(AmoraCore)
    core._closed = False
    core.secrets = SimpleNamespace(get=lambda key: "fictional-token" if key == "LOCAL_API_TOKEN" else None)
    core.database = SimpleNamespace(
        expected_schema_version=18,
        schema_version=lambda: 18,
        backend=SimpleNamespace(capabilities=SimpleNamespace(name="postgres-candidate")),
        integrity_check=lambda: status,
    )
    return core


def test_postgres_core_readiness_never_runs_sqlite_update():
    assert _core("ok").readiness() == {
        "status": "ready", "storage": "postgres-candidate",
    }
    report = _core("postgres-candidate-unverified").readiness()
    assert report == {"status": "not_ready", "error_code": "STORAGE_UNAVAILABLE"}
