"""Run 2: PG18 queue claim/ack/lease idempotency through real stores.

No external Discord transport: all target addresses and events are invented.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import os
from pathlib import Path
from uuid import UUID

import pytest

from amora.messaging.generation_lifecycle import InboundGeneration,InboundGenerationLost
from amora.messaging.models import MessagingPlatform,OutboundMessage,OutboundStatus
from amora.messaging.postgres_store import PostgresMessagingStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Only local disposable PG18, no Discord or production",
)
STAMP=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )

def test_pg_outbound_lease_concurrent_claim_ack_and_restart(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=4)
            clock=SimulatedClock(STAMP)
            path=tmp_path/"synthetic.db"
            with closing(AmoraDatabase(path,backend=backend)) as first,closing(
                AmoraDatabase(path,backend=backend)
            ) as second:
                first.migrate()
                a=PostgresMessagingStore(first,clock)
                b=PostgresMessagingStore(second,clock)
                lease=InboundGeneration(first,clock)
                assert lease.claim(
                    "synthetic-turn","session-test","a"*20,1,"lease-1",
                ) is True
                assert lease.claim(
                    "synthetic-turn","session-test","a"*20,1,"lease-2",
                ) is False
                msg=OutboundMessage(
                    id=UUID(int=712),platform=MessagingPlatform.DISCORD,
                    account_scope="synthetic",
                    conversation_id="synthetic-direct-conversation",
                    target_entity_id="PERSON_BK",
                    text="mensagem inventada de teste",created_at=STAMP,
                )
                with pytest.raises(InboundGenerationLost):
                    a.put_outbound(msg,generation_lease=(
                        "synthetic-turn","invalid-lease",
                    ))
                assert a.get_outbound(msg.id) is None
                assert a.put_outbound(
                    msg,generation_lease=("synthetic-turn","lease-1"),
                ) is True
                assert a.put_outbound(msg) is False
                with ThreadPoolExecutor(max_workers=2) as pool:
                    future_a=pool.submit(a.claim_outbound,
                        platform=MessagingPlatform.DISCORD,
                        account_scope="synthetic")
                    future_b=pool.submit(b.claim_outbound,
                        platform=MessagingPlatform.DISCORD,
                        account_scope="synthetic")
                    claims=(*future_a.result(),*future_b.result())
                assert len(claims)==1
                assert claims[0].id==msg.id
                assert claims[0].status==OutboundStatus.CLAIMED
                assert a.ack_outbound(
                    msg.id,sent=True,external_message_id="synthetic-delivered",
                ) is True
                assert b.get_outbound(msg.id).status==OutboundStatus.SENT
                assert lease.release("synthetic-turn","lease-1",completed=True) is True
                assert a.pending_outbound_count()==0
            assert backend.opened_connections==0
            with closing(AmoraDatabase(path,backend=backend)) as restarted:
                b=PostgresMessagingStore(restarted,clock)
                assert b.get_outbound(UUID(int=712)).status==OutboundStatus.SENT
                assert b.claim_outbound(
                    platform=MessagingPlatform.DISCORD,account_scope="synthetic"
                )==()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
