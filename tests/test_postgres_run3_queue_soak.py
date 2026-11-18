"""Run 3: bounded multi-worker PG18 queue soak and restart recovery.

Disposable localhost PostgreSQL only. No Discord socket, credentials,
user records or actual network sends. The time bound is a generous CI
regression guard, not a production performance SLA.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from contextlib import ExitStack,closing
from datetime import datetime,timezone
import os
from pathlib import Path
from time import monotonic
from uuid import UUID

import pytest

from amora.messaging.models import MessagingPlatform,OutboundMessage,OutboundStatus
from amora.messaging.postgres_store import PostgresMessagingStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Disposable localhost PG18 queue only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)
TOTAL=96
WORKERS=4


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg18_four_worker_96_message_queue_soak(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=8)
            clock=SimulatedClock(NOW)
            path=tmp_path/"synthetic-queue.db"
            started=monotonic()
            with ExitStack() as stack:
                databases=[
                    stack.enter_context(closing(AmoraDatabase(path,backend=backend)))
                    for _ in range(WORKERS)
                ]
                databases[0].migrate()
                stores=[PostgresMessagingStore(db,clock) for db in databases]
                ids={str(UUID(int=90000+i)) for i in range(TOTAL)}
                for i in range(TOTAL):
                    message=OutboundMessage(
                        id=UUID(int=90000+i),
                        platform=MessagingPlatform.DISCORD,
                        account_scope="synthetic-run3",
                        conversation_id="synthetic-owner-dm",
                        target_entity_id="PERSON_BK",
                        text=f"synthetic output {i}",created_at=NOW,
                    )
                    assert stores[0].put_outbound(message) is True
                assert stores[0].pending_outbound_count()==TOTAL

                rendezvous=Barrier(WORKERS,timeout=15)

                def drain(store:PostgresMessagingStore):
                    # Force all four independent PG connections to start
                    # competing for claims at the same time.
                    rendezvous.wait(timeout=15)
                    consumed=[]
                    for _ in range(TOTAL):
                        batch=store.claim_outbound(
                            platform=MessagingPlatform.DISCORD,
                            account_scope="synthetic-run3",limit=8,
                        )
                        if not batch:
                            break
                        for item in batch:
                            assert item.status==OutboundStatus.CLAIMED
                            assert store.ack_outbound(
                                item.id,sent=True,
                                external_message_id="synthetic-ack-"+str(item.id),
                            ) is True
                            consumed.append(str(item.id))
                    return consumed

                with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                    futures=[pool.submit(drain,store) for store in stores]
                    results=[f.result(timeout=75) for f in futures]
                delivered=[item for result in results for item in result]
                assert len(delivered)==TOTAL
                assert len(set(delivered))==TOTAL
                assert set(delivered)==ids
                assert stores[0].pending_outbound_count()==0
                assert stores[1].claim_outbound(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="synthetic-run3",
                )==()
                assert all(
                    stores[2].get_outbound(UUID(item)).status==OutboundStatus.SENT
                    for item in (delivered[0],delivered[-1])
                )
            assert backend.opened_connections==0
            with closing(AmoraDatabase(path,backend=backend)) as restarted:
                store=PostgresMessagingStore(restarted,clock)
                assert store.pending_outbound_count()==0
                assert all(
                    store.get_outbound(UUID(item)).status==OutboundStatus.SENT
                    for item in ids
                )
            assert backend.opened_connections==0
            assert monotonic()-started<90, "PG18_RUN3_QUEUE_SOAK_REGRESSION"
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
