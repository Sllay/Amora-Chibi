"""One-shot Aiven real-memory write contracts against disposable PostgreSQL 18."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from uuid import UUID

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.memory.models import (
    MemoryKind, MemoryProvenance, MemoryRecord, MemoryQuery, ProvenanceKind,
)
from amora.storage import aiven_memory_write_probe as probe
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock


def test_write_cli_requires_confirmation_and_never_opens_network(capsys):
    assert probe.main([]) == 2
    line = capsys.readouterr().out
    assert line.startswith("AIVEN_MEMORY_WRITE_TEST ")
    report = json.loads(line.split(" ", 1)[1])
    assert report["reason"] == "PG_WRITE_PROBE_CONFIRMATION_REQUIRED"
    assert report["production_cutover_authorized"] is False


@pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 fixture only",
)
def test_fictional_private_memory_survives_reconnect_and_is_archived(
    monkeypatch, tmp_path,
):
    psycopg = pytest.importorskip("psycopg")

    def raw():
        return psycopg.connect(
            host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
            user="amora_ci", password="synthetic_only",
            connect_timeout=5, autocommit=True,
        )

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AMORA_MODE", "local")
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    with closing(raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend = synthetic_core_backend(raw, max_connections=16)
            core = AmoraCore.bootstrap(
                clock=SimulatedClock(now), storage=backend,
            )
            old_id = UUID(int=851906617)
            try:
                prior = MemoryRecord(
                    id=old_id,
                    kind=MemoryKind.EPISODIC,
                    content="perolaazulantiga memoria ficticia historica",
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.OBSERVATION,
                        source="synthetic:aiven-prior-memory",
                        actor_id="PERSON_BK",
                    ),
                    privacy_scope=PrivacyScope.PRIVATE,
                    owner_entity_id="PERSON_BK",
                    remote_processing_allowed=False,
                    created_at=now,
                    updated_at=now,
                    importance=0.6,
                    gate_score=0.8,
                )
                assert core.memory.store.put(prior, insert_only=True) is True
                events_before = core.database.event_count()
                identities_before = len(core.identities.entities)
            finally:
                core.close()
            assert backend.opened_connections == 0

            result = probe.continuity_with_backend(
                backend,
                expected_events=events_before,
                expected_identity_count=identities_before,
                expected_prior_memories=1,
            )
            assert result["status"] == "aiven_staging_new_memory_write_reconnect_pass"
            assert result["owner_recall_pass"] is True
            assert result["unrelated_principal_rejected"] is True
            assert result["fictional_memory_archived_after_test"] is True

            restarted = AmoraCore.bootstrap(
                clock=SimulatedClock(now), storage=backend,
            )
            try:
                assert restarted.memory.store.get(old_id).content == prior.content
                assert any(hit.memory.id == old_id for hit in restarted.memory.store.rank(
                    MemoryQuery(
                        text="perolaazulantiga",
                        projection_recipient_id="PERSON_BK",
                        require_topic_match=True,
                    )
                ))
                synthetic = restarted.memory.store.get(probe._PROBE_ID)
                assert synthetic is not None
                assert synthetic.archived_at is not None
                assert restarted.memory.store.count() == 1
                assert restarted.memory.store.count(include_archived=True) == 2
            finally:
                restarted.close()
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
