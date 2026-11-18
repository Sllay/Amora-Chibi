"""Run 2: exercise complete AmoraCore bootstrap with the real PG18 candidate.

All records invented. Production/Aiven/Turso are never selected or accessed.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os

import pytest

from amora.app import AmoraCore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires local disposable PostgreSQL 18 CI service",
)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_full_amora_core_bootstrap_restart_and_health(tmp_path:Path,monkeypatch):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"amora-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    monkeypatch.delenv("AMORA_SECRET_LOCAL_API_TOKEN",raising=False)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=16)
            clock=SimulatedClock(datetime(2026,11,17,tzinfo=timezone.utc))
            first=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                report=first.health_report()
                assert report["schema_version"]==18
                assert report["database_integrity"]=="postgres-candidate-unverified"
                assert report["status"]=="degraded"  # never fake certified
                assert report["self_core_identity"]=="ENTITY_AMORA"
                assert len(report["drives"])==7
                assert report["cognitive_usage_ledger"]==[]
                first.cognition.store.record_usage(
                    provider="synthetic-run2",model="invented",
                    task_class="social",success=True,
                    input_tokens=3,output_tokens=4,status_code=200,
                )
                ledger=first.cognition.store.usage_ledger()
                assert len(ledger)==1
                assert ledger[0]["success_count"]==1
                assert ledger[0]["input_tokens"]==3
                assert first.cognition.store.latest_usage(
                    "synthetic-run2",task_class="social",
                )["status_code"]==200
            finally:
                first.close()
            assert backend.opened_connections==0
            second=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                again=second.health_report()
                assert again["self_core_identity"]=="ENTITY_AMORA"
                assert again["cognitive_usage_ledger"][0]["output_tokens"]==4
            finally:
                second.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
