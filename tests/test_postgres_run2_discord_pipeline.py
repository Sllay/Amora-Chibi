"""Run 2: fully synthetic Discord Core route on real PG18 stores.

No network Discord send and no live LLM. Fake provider only, persisted native
events and inspected outbound queue, with edit invalidation and ACK fencing.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
import os

import pytest

from amora.app import AmoraCore
from amora.messaging.models import (
    ChannelType,ExternalActorRef,InboundMessage,
    MessagingPlatform,RoutingAction,TransportInvalidation,OutboundStatus,
)
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock
from test_messaging_lifecycle_stability import CountingStructuredProvider
from test_platform_messaging_step2 import _bind_bk

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Local disposable PG18, no actual Discord or providers",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def _dm(*,message_id,revision=1,sender="111111111111111111",content="Oi Amora, responda!"):
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",conversation_id="synthetic-discord-dm",
        message_id=message_id,
        native_message_id="synthetic-original-id",
        transport_revision=revision,
        channel_type=ChannelType.DM,
        sender_identifiers=[ExternalActorRef(
            identifier_type="discord_user_id",
            external_actor_id=sender,
        )],
        text=content,timestamp=NOW,remote_processing_allowed=True,
    )


@pytest.mark.asyncio
async def test_pg_discord_unknown_verified_duplicate_edit_and_ack(tmp_path,monkeypatch):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"amora-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=18)
            core=AmoraCore.bootstrap(clock=SimulatedClock(NOW),storage=backend)
            try:
                provider=CountingStructuredProvider()
                core.cognition.register_provider(provider)
                unknown=await core.messaging.receive(_dm(
                    message_id="unknown",sender="999999999999999999",
                ))
                assert unknown.decision.action==RoutingAction.QUARANTINE
                assert not unknown.outbound
                _bind_bk(core)
                accepted=await core.messaging.receive(_dm(message_id="first"))
                assert accepted.decision.action==RoutingAction.RESPOND
                assert len(accepted.outbound)==1
                outbound=accepted.outbound[0]
                assert outbound.target_entity_id=="PERSON_BK"
                repeat=await core.messaging.receive(_dm(message_id="first"))
                assert repeat.duplicate is True
                assert repeat.outbound[0].id==outbound.id
                assert core.messaging_store.get_outbound(
                    outbound.id
                ).status==OutboundStatus.QUEUED

                edited=core.messaging.invalidate_transport(TransportInvalidation(
                    platform=MessagingPlatform.DISCORD,account_scope="primary",
                    conversation_id="synthetic-discord-dm",
                    native_message_id="synthetic-original-id",
                    reason="MESSAGE_EDITED",transport_revision=2,
                ))
                assert edited["status"]=="ok"
                assert edited["cancelled_outbound"]>=1
                assert core.messaging_store.get_outbound(
                    outbound.id
                ).status==OutboundStatus.CANCELLED
                assert core.messaging_store.claim_outbound(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",
                )==()

                newer=await core.messaging.receive(_dm(
                    message_id="edited-r2",revision=2,
                    content="Amora, agora uma pergunta diferente.",
                ))
                assert newer.decision.action==RoutingAction.RESPOND
                assert len(newer.outbound)==1
                answer=newer.outbound[0]
                assert answer.id!=outbound.id
                claimed=core.messaging_store.claim_outbound(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",
                )
                assert [item.id for item in claimed]==[answer.id]
                assert core.messaging_store.ack_outbound(
                    answer.id,sent=True,
                    external_message_id="synthetic-confirmation",
                ) is True
                assert core.messaging_store.get_outbound(
                    answer.id
                ).status==OutboundStatus.SENT
                assert core.messaging_store.pending_outbound_count()==0
            finally:
                core.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
