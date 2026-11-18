"""Injected PostgreSQL EmotionStore with fixed, parameterized list queries."""
from __future__ import annotations

from pathlib import Path
from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_emotion import EVENT_LIST,BID_LIST
from amora.time import Clock

from .store import EmotionStore
from .models import EmotionEvent,SocialBid,SocialBidStatus


class PostgresEmotionStore(EmotionStore):
    def __init__(self,path:Path,clock:Clock,*,backend:StorageBackend):
        if backend.capabilities.name!="postgres-candidate":
            raise PostgresRuntimeError("PG_EMOTION_BACKEND_REQUIRED")
        super().__init__(path,clock,backend=backend)

    def list_events(self,*,target_id:str|None=None,limit:int=50)->list[EmotionEvent]:
        with self._lock:
            rows=self._conn.execute(
                EVENT_LIST,(target_id,target_id,max(1,min(200,int(limit)))),
            ).fetchall()
        return [self._hydrate_event(row) for row in rows]

    def list_bids(self,*,include_expired:bool=False,limit:int=50)->list[SocialBid]:
        now=self.clock.now().isoformat()
        with self._lock,self._conn:
            self._conn.execute(
                "UPDATE social_bids SET status = ? WHERE status = ? AND expires_at <= ?",
                (SocialBidStatus.EXPIRED.value,SocialBidStatus.PENDING.value,now),
            )
            rows=self._conn.execute(BID_LIST,(
                bool(include_expired),SocialBidStatus.PENDING.value,
                max(1,min(200,int(limit))),
            )).fetchall()
        return [self._hydrate_bid(row) for row in rows]
