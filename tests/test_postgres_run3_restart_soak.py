"""Run 3: bounded real-PG18 soak, failed bootstrap cleanup, no cutover."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os

import pytest

from amora.app import AmoraCore
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable localhost PostgreSQL 18 only; no external services",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_run3_repeated_core_start_close_keeps_pg_budget_and_history(tmp_path,monkeypatch):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"synthetic-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    monkeypatch.delenv("AMORA_SECRET_LOCAL_API_TOKEN",raising=False)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=16)
            clock=SimulatedClock(NOW)
            for cycle in range(5):
                core=AmoraCore.bootstrap(clock=clock,storage=backend)
                try:
                    health=core.health_report()
                    assert health["status"]=="degraded"
                    assert health["database_integrity"]=="postgres-candidate-unverified"
                    assert health["schema_version"]==18
                    assert health["self_core_identity"]=="ENTITY_AMORA"
                    ledger=core.cognition.store.usage_ledger()
                    assert sum(row["request_count"] for row in ledger)==cycle
                    core.cognition.store.record_usage(
                        provider="run3-invented",model="synthetic",
                        task_class="social",success=True,
                        input_tokens=cycle+1,output_tokens=2,
                        status_code=200,
                    )
                finally:
                    core.close()
                assert backend.opened_connections==0
            restarted=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                latest=restarted.cognition.store.latest_usage(
                    "run3-invented",task_class="social",
                )
                assert latest is not None
                assert latest["input_tokens"]==5
                ledger=restarted.cognition.store.usage_ledger()
                assert sum(row["request_count"] for row in ledger)==5
            finally:
                restarted.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_run3_failed_bootstrap_releases_every_open_connection(tmp_path,monkeypatch):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"failed-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    monkeypatch.delenv("AMORA_SECRET_LOCAL_API_TOKEN",raising=False)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=16)

            class FailOnce:
                capabilities=backend.capabilities
                attempts=0

                def connect(self,path):
                    self.attempts+=1
                    if self.attempts==4:
                        raise PostgresRuntimeError("PG_RUN3_SYNTHETIC_BOOT_FAILURE")
                    return backend.connect(path)

                def backup(self,connection,destination):
                    return backend.backup(connection,destination)

            injected=FailOnce()
            with pytest.raises(PostgresRuntimeError,match="PG_RUN3_SYNTHETIC_BOOT_FAILURE"):
                AmoraCore.bootstrap(clock=SimulatedClock(NOW),storage=injected)
            assert injected.attempts==4
            assert backend.opened_connections==0
            # A clean retry with unchanged schema and identity must recover,
            # not trigger an implicit SQLite/libSQL fallback.
            core=AmoraCore.bootstrap(clock=SimulatedClock(NOW),storage=backend)
            try:
                assert core.health_report()["schema_version"]==18
                assert core.health_report()["status"]=="degraded"
            finally:
                core.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
