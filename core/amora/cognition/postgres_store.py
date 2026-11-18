"""PostgreSQL candidate cognition store with reviewed dynamic history query."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_cognition import HISTORY
from amora.time import Clock

from .store import CognitiveStore


class PostgresCognitiveStore(CognitiveStore):
    def __init__(self, database_path: Path, clock: Clock, *,
                 backend: StorageBackend) -> None:
        if backend.capabilities.name != "postgres-candidate":
            raise PostgresRuntimeError("PG_COGNITION_BACKEND_REQUIRED")
        super().__init__(database_path,clock,backend=backend)

    def start_async_lane(self):
        # Hrana-specific asynchronous queue assumes libSQL transport/session
        # behavior. Keep synchronous transactional writes until reviewed for PG.
        return None

    def list_messages(
        self, *, limit: int = 40, speaker_id: str | None = None,
        turn_id: UUID | str | None = None,
        session_id: str | None = None,
        exclude_invalidated: bool = False,
    ):
        turn = str(turn_id) if turn_id is not None else None
        with self._lock:
            rows = self._conn.execute(HISTORY,(
                speaker_id,speaker_id,turn,turn,session_id,session_id,
                bool(exclude_invalidated),max(1,min(500,int(limit))),
            )).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]
