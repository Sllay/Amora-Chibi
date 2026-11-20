"""PostgreSQL candidate cognition store with reviewed dynamic history query."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_cognition import HISTORY,LATEST_USAGE,MERGE_METADATA
from amora.time import Clock

from .store_v1 import CognitiveStore


class PostgresCognitiveStore(CognitiveStore):
    def __init__(self, database_path: Path, clock: Clock, *,
                 backend: StorageBackend) -> None:
        if backend.capabilities.name != "postgres-candidate":
            raise PostgresRuntimeError("PG_COGNITION_BACKEND_REQUIRED")
        super().__init__(database_path,clock,backend=backend)

    def latest_usage(self,provider:str,*,task_class:str|None=None):
        with self._lock:
            row=self._conn.execute(
                LATEST_USAGE,(provider,task_class,task_class),
            ).fetchone()
        return dict(row) if row is not None else None

    def merge_message_metadata(self,message_id:UUID|str,values:dict[str,object])->bool:
        if not values:
            return True
        # Atomic JSONB patch: a SELECT then UPDATE would silently overwrite
        # metadata written by another worker between the two statements.
        with self._lock,self._conn:
            updated=self._conn.execute(
                MERGE_METADATA,(
                    json.dumps(values,ensure_ascii=False,separators=(",",":")),
                    str(message_id),
                ),
            )
        return updated.rowcount==1

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
                int(exclude_invalidated),max(1,min(500,int(limit))),
            )).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]
