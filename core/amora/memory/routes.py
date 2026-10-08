from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status

from amora.app import AmoraCore

from .models import MemoryCandidate, MemoryQuery


def register_memory_routes(
    app: FastAPI,
    core: AmoraCore,
    require_auth: Callable[..., None],
) -> None:
    protected = [Depends(require_auth)]

    @app.post("/v1/memory/remember", dependencies=protected)
    def remember(candidate: MemoryCandidate) -> dict[str, object]:
        result = core.memory.remember(candidate)
        return {
            "decision": result.decision.model_dump(mode="json"),
            "memory": result.memory.model_dump(mode="json") if result.memory else None,
        }

    @app.post("/v1/memory/recall", dependencies=protected)
    def recall(query: MemoryQuery) -> list[dict[str, object]]:
        return [hit.model_dump(mode="json") for hit in core.memory.recall(query)]

    @app.get("/v1/memory/{memory_id}", dependencies=protected)
    def get_memory(memory_id: UUID) -> dict[str, object]:
        memory = core.memory.get(memory_id)
        if memory is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
        return memory.model_dump(mode="json")

    @app.post("/v1/memory/{memory_id}/forget", dependencies=protected)
    def forget_memory(memory_id: UUID, factor: float = 0.8) -> dict[str, object]:
        if factor < 0.0 or factor > 1.0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="factor must be between 0 and 1")
        value = core.memory.forget_softly(memory_id, factor=factor)
        if value is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
        return {"memory_id": str(memory_id), "retrievability": value}
