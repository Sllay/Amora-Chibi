"""Fail-closed unit checks; no PostgreSQL driver or network required."""
from types import SimpleNamespace

import pytest

from amora.storage import postgres_memory_lab as lab


class FakeConnection:
    def __init__(self, *, host="127.0.0.1", dbname="amora_pg_synthetic", version=180001):
        self.info = SimpleNamespace(host=host, dbname=dbname, server_version=version)
        self.sql_calls = 0

    def execute(self, *args, **kwargs):
        self.sql_calls += 1
        raise AssertionError("rejected lab must not execute SQL")


@pytest.mark.parametrize("settings", [
    {"host": "db.example.aivencloud.com"},
    {"host": "10.0.0.1"},
    {"dbname": "defaultdb"},
    {"dbname": "amora_live"},
    {"version": 170003},
    {"version": 190001},
])
def test_local_lab_gate_refuses_real_or_incompatible_database_before_sql(settings):
    conn = FakeConnection(**settings)
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        lab.install_lab(conn)
    assert conn.sql_calls == 0


def test_phase2a_is_partial_schema_not_prod_backend():
    joined = "\n".join(lab.LAB_DDL)
    assert "amora_pg_lab.memory_records" in joined
    assert "amora_pg_lab.memory_entities" in joined
    assert "amora_pg_lab.memory_tags" in joined
    assert "amora_pg_lab.memory_links" in joined
    assert "REFERENCES amora_pg_lab.memory_records(id) ON DELETE CASCADE" in joined
    assert "USING GIN" in joined
    assert "to_tsvector('simple'::regconfig" in joined
    assert "DROP TABLE" not in joined
    assert "AMORA_STORAGE_BACKEND" not in joined
    assert "aivencloud.com" not in joined
    assert not hasattr(lab, "backend_from_environment")


def test_local_lab_rejects_unbounded_query_without_sql():
    conn = FakeConnection()
    for n in (-1, 0, 151, 1_000_000):
        with pytest.raises(ValueError, match="POSTGRES_LAB_INVALID_LIMIT"):
            lab.candidate_ids(conn, owner="BK", query="rabbit", limit=n)
    assert conn.sql_calls == 0
