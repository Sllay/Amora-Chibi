from __future__ import annotations

from pathlib import Path

import httpx

from amora.app import AmoraCore
from amora.memory import MemoryQuery
from amora.storage.remote import LibSQLBackend
from amora.world import EpistemicKind, WorldClaimDraft
from test_memory_v03 import important_candidate
from test_remote_storage import HranaSQLite


class CountingHrana(HranaSQLite):
    def __init__(self, path: Path):
        super().__init__(path)
        self.pipeline_calls = 0

    def __call__(self, request):
        self.pipeline_calls += 1
        return super().__call__(request)


def _remote_core(tmp_path: Path, monkeypatch) -> tuple[AmoraCore, CountingHrana]:
    server = CountingHrana(tmp_path / "context-batching.db")
    backend = LibSQLBackend(
        "https://database.example",
        "test-only",
        transport=httpx.MockTransport(server),
    )
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "remote-home"))
    return AmoraCore.bootstrap(storage=backend), server


def test_remote_memory_recall_hydrates_candidates_in_bounded_pipelines(tmp_path: Path, monkeypatch) -> None:
    core, server = _remote_core(tmp_path, monkeypatch)
    try:
        template = important_candidate()
        for index in range(24):
            candidate = template.model_copy(
                update={
                    "content": f"BK mostrou um desenho importante numero {index}",
                    "summary": f"desenho importante {index}",
                    "tags": {"desenho", f"serie-{index}"},
                }
            )
            assert core.memory.remember(candidate).memory is not None

        server.pipeline_calls = 0
        hits = core.memory.recall(
            MemoryQuery(text="desenho importante", entity_ids={"PERSON_BK"}, limit=8)
        )

        assert len(hits) == 8
        assert all("desenho" in hit.memory.tags for hit in hits)
        assert all("PERSON_BK" in hit.memory.entity_refs for hit in hits)
        # Candidate count must not multiply Hrana requests. The budget includes
        # candidate + child bulk reads + FTS/entity ranks + one atomic access update.
        assert server.pipeline_calls <= 10
    finally:
        core.close()


def test_remote_world_state_hydrates_evidence_in_constant_pipelines(tmp_path: Path, monkeypatch) -> None:
    core, server = _remote_core(tmp_path, monkeypatch)
    try:
        for index in range(24):
            result = core.world.record(
                WorldClaimDraft(
                    kind=EpistemicKind.BELIEF,
                    statement=f"BK prefere detalhe visual {index}",
                    subject_id="PERSON_BK",
                    predicate=f"visual_preference_{index}",
                    object_value=index,
                    source="test:remote-context-batching",
                    confidence=0.98,
                )
            )
            assert result.claim.canonical is True

        server.pipeline_calls = 0
        state = core.world.current_state(subject_id="PERSON_BK")

        assert len(state) == 24
        assert {item.predicate for item in state} == {f"visual_preference_{index}" for index in range(24)}
        # One claim query + one bulk evidence query, independent of claim count.
        assert server.pipeline_calls <= 2
    finally:
        core.close()
