"""PCS.3A: pure retrieval, one authoritative access write and explicit injection."""
from pathlib import Path
import sqlite3

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.latency_trace import bind_latency_trace
from amora.memory import MemoryQuery
from amora.memory.projection import ReplicaMemoryReadView, VerifiedMemoryRecall
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import StorageUnavailable
from amora.storage.replica_read import ReplicaReadBackend
from amora.storage.replica_coherence import VersionedReplicaReadGroup, read_projection_stamp
from test_memory_v03 import important_candidate
from test_messaging_lifecycle_stability import _clock
from test_pcs1_local_storage_lab import request
from test_pcs2_native_schema import install_lab_epoch


class NativeSnapshot:
    """Test double for native sync; copies committed state into an isolated DB."""
    def __init__(self, source):
        self.source = source
        self.connection = sqlite3.connect(":memory:")

    def sync(self):
        with sqlite3.connect(self.source) as source:
            source.backup(self.connection)

    def execute(self, *args):
        return self.connection.execute(*args)

    def close(self):
        self.connection.close()


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "primary"))
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(clock=_clock(), storage=SQLiteBackend())
    view = None
    try:
        memory = core.memory.remember(important_candidate(
            content="Desenho aurora: projeto sintético de programação.",
            privacy_scope=PrivacyScope.PUBLIC, remote_processing_allowed=True)).memory
        assert memory
        install_lab_epoch(core.database._conn)
        backend = ReplicaReadBackend(lambda _: NativeSnapshot(core.database.path))
        view = ReplicaMemoryReadView(Path("unused"), core.clock, core.identities, backend=backend)
        group = VersionedReplicaReadGroup(backend, lambda: read_projection_stamp(core.database._conn))
        projection = VerifiedMemoryRecall(core.memory.store, view, group)
        query = MemoryQuery(text="desenho aurora", entity_ids={"PERSON_BK"},
                            projection_recipient_id="PERSON_BK", projection_remote_only=True)
        yield core, memory, projection, query
    finally:
        if view is not None:
            view.close()
        core.close()


def test_pcs3_pure_rank_and_verified_access_exactly_once(lab):
    core, memory, projection, query = lab
    primary = core.memory.store
    count = primary.get(memory.id).access_count
    ranked = primary.rank(query)
    assert [hit.memory.id for hit in ranked] == [memory.id]
    assert primary.get(memory.id).access_count == count
    with bind_latency_trace() as trace:
        hits = projection.recall(query)
    assert trace["pcs_memory_projection_used"] == 1
    assert hits[0].memory.access_count == count + 1
    assert primary.get(memory.id).access_count == count + 1
    assert hits[0].score == ranked[0].score
    assert hits[0].reason_codes == ranked[0].reason_codes
    assert hits[0].memory.last_accessed_at == core.clock.now()


def test_pcs3_revocation_between_verified_read_and_access_discards_result(lab, monkeypatch):
    core, memory, projection, query = lab
    primary = core.memory.store
    count = primary.get(memory.id).access_count
    original = primary.record_verified_access
    def race(hits, stamp):
        # Independent authoritative writer revokes permission before access.
        with sqlite3.connect(core.database.path) as writer:
            writer.execute("UPDATE memory_records SET remote_processing_allowed=0 WHERE id=?",
                           (str(memory.id),))
        return original(hits, stamp)
    monkeypatch.setattr(primary, "record_verified_access", race)
    with bind_latency_trace() as trace:
        assert projection.recall(query) == []
    assert trace["pcs_memory_projection_revision_fallback"] == 1
    assert primary.get(memory.id).access_count == count


def test_pcs3_unknown_access_commit_is_not_replayed(lab, monkeypatch):
    core, _, projection, query = lab
    primary = core.memory.store
    def ambiguous(*args):
        raise StorageUnavailable("SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN")
    def forbidden(*args):
        pytest.fail("ambiguous access commit must not be replayed")
    monkeypatch.setattr(primary, "record_verified_access", ambiguous)
    monkeypatch.setattr(primary, "search", forbidden)
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        projection.recall(query)


def test_pcs3_context_uses_projection_only_for_ordinary_remote_dm(lab, monkeypatch):
    core, memory, projection, _ = lab
    compiler = core.cognition.compiler
    compiler.memory_projection = projection
    req = request("O que acha do desenho aurora?", 900)
    with bind_latency_trace() as trace:
        context = compiler.compile(req)
    assert trace["pcs_memory_projection_used"] == 1
    assert str(memory.id) in {item.id for item in context.memories}

    def forbidden(*args):
        pytest.fail("projection must not be selected for a sensitive/non-DM path")
    monkeypatch.setattr(projection, "recall", forbidden)
    variants = (
        req.model_copy(update={"remote_processing_allowed": False}),
        req.model_copy(update={"metadata": {**req.metadata, "channel_type": "group"}}),
        req.model_copy(update={"metadata": {**req.metadata, "cognitive_mode": "reflection"}}),
        req.model_copy(update={"metadata": {**req.metadata, "explicit_memory_recall": True}}),
        req.model_copy(update={"metadata": {**req.metadata, "speech_device_id": "fixture"}}),
        req.model_copy(update={"text": "Amora, lembra do desenho aurora?"}),
    )
    for variant in variants:
        compiler.compile(variant)
