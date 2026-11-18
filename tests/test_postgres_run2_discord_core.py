"""Run 2: Discord-like inbound to actual AmoraCore in isolated PG18.

No Discord socket, provider keys, real accounts or production data. The actual
PlatformMessagingService, identity resolution, cognition, audit and outbound
store are exercised. Multi-worker queue claims remain separately tested.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
import os

import pytest

from amora.app import AmoraCore
from amora.identity import BindingTrust,PlatformIdentityBinding,PlatformKind
from amora.messaging import (
    ChannelType,ExternalActorRef,InboundMessage,MessagingPlatform,RoutingAction,
)
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="No external Discord; local disposable PG18 service only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)
ACTOR="111111111111111111"


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def _incoming(*,id,actor=ACTOR,channel=ChannelType.DM,reply=False):
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,account_scope="primary",
        conversation_id="pg18-private" if channel==ChannelType.DM else "pg18-group",
        message_id=id,channel_type=channel,
        sender_identifiers=[ExternalActorRef(
            identifier_type="discord_user_id",external_actor_id=actor,
        )],
        text="Amora, responde sobre este registro ficticio",
        reply_to_amora=reply,
        reply_to_message_id="synthetic-previous" if reply else None,
        timestamp=NOW,remote_processing_allowed=False,
    )


def _bind_owner(core):
    core.profiles.upsert_binding(PlatformIdentityBinding(
        platform=PlatformKind.DISCORD,
        identifier_type="discord_user_id",external_actor_id=ACTOR,
        entity_id="PERSON_BK",account_scope="primary",
        trust=BindingTrust.OPERATOR_VERIFIED,confidence=1.0,
    ))


@pytest.mark.asyncio
async def test_pg18_full_discord_like_inbound_identity_outbound_and_restart(
    tmp_path,monkeypatch,
):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"synthetic-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    for key in ("NVIDIA_API_KEY","GROQ_API_KEY","GEMINI_API_KEY","OPENROUTER_API_KEY","NUMES_API_KEY"):
        monkeypatch.delenv("AMORA_SECRET_"+key,raising=False)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=20)
            clock=SimulatedClock(NOW)
            core=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                blocked=await core.messaging.receive(_incoming(
                    id="pg18-unknown",actor="222222222222222222",
                ))
                assert blocked.decision.action==RoutingAction.QUARANTINE
                assert blocked.outbound==[]
                _bind_owner(core)
                incoming=_incoming(id="pg18-owner-inbound")
                first=await core.messaging.receive(incoming)
                assert first.decision.action==RoutingAction.RESPOND
                assert first.decision.speaker_entity_id=="PERSON_BK"
                assert len(first.outbound)==1
                outgoing=first.outbound[0]
                assert outgoing.source_message_id=="pg18-owner-inbound"
                repeated=await core.messaging.receive(incoming)
                assert repeated.duplicate is True
                assert len(repeated.outbound)==1
                assert repeated.outbound[0].id==outgoing.id
                claimed=core.messaging_store.claim_outbound(
                    platform=MessagingPlatform.DISCORD,account_scope="primary",
                )
                assert [item.id for item in claimed]==[outgoing.id]
                assert core.messaging_store.ack_outbound(
                    outgoing.id,sent=True,
                    external_message_id="synthetic-discord-sent",
                ) is True
                assert core.messaging_store.pending_outbound_count()==0
            finally:
                core.close()
            assert backend.opened_connections==0
            restarted=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                _bind_owner(restarted)
                assert restarted.messaging_store.get_outbound(outgoing.id).status.value=="sent"
                third=await restarted.messaging.receive(incoming)
                assert third.duplicate is True
                assert restarted.messaging_store.pending_outbound_count()==0
                assert not third.outbound or all(
                    m.id==outgoing.id for m in third.outbound
                )
                # A private DM audit must not automatically appear in the
                # unrelated group's context or grant a publication right.
                group=_incoming(id="pg18-group-check",channel=ChannelType.GROUP)
                observed=await restarted.messaging.receive(group)
                assert all(
                    "pg18-owner-inbound" not in m.text
                    for m in observed.outbound
                )
            finally:
                restarted.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
