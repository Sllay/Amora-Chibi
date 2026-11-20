"""Real PG18 expression-memory ingestion, visual lease and semantic reuse.

No external media, screenshot fetch, provider call or user records.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
import os

import pytest

from amora.messaging.expression_memory import (
    ExpressionSemanticStatus,PerceptualExpressionMemory,
)
from amora.messaging.models import (
    AttentionDecision,ChannelType,ExpressionKind,ExpressionRef,
    ExpressionSemanticProfile,ExternalActorRef,InboundMessage,
    MessagingPlatform,RoutingAction,
)
from amora.messaging.postgres_store import PostgresMessagingStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Disposable local PG18 expression-store regression only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_expression_metadata_claim_visual_enrichment_and_restart(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=3)
            clock=SimulatedClock(NOW)
            path=tmp_path/"synthetic.db"
            expression=ExpressionRef(
                kind=ExpressionKind.STICKER,
                native_id="sticker-1234",name="Coelho azul",
                description="coelho estilizado",tags=["coelho","surpreso"],
            )
            message=InboundMessage(
                platform=MessagingPlatform.DISCORD,
                account_scope="primary",conversation_id="fictional-guild",
                message_id="fictional-msg-1",channel_type=ChannelType.GROUP,
                sender_identifiers=[ExternalActorRef(
                    identifier_type="discord_user_id",
                    external_actor_id="111111111111111111",
                )],
                text="Amora, olha esse adesivo",
                expressions=[expression],timestamp=NOW,
            )
            decision=AttentionDecision(
                action=RoutingAction.RESPOND,speaker_entity_id="PERSON_BK",
                speaker_actor_ref="synthetic-bk",
            )
            with closing(AmoraDatabase(path,backend=backend)) as db:
                db.migrate()
                store=PerceptualExpressionMemory(
                    PostgresMessagingStore(db,clock),clock,
                )
                seen=store.observe_relevant(message,decision)
                assert seen.expressions[0].known_to_amora is True
                assert store.known_count()==1
                item=store.get(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",scope_id="fictional-guild",
                    kind="sticker",native_id="sticker-1234",
                )
                assert item.semantic_status==ExpressionSemanticStatus.PENDING_VISUAL
                assert item.seen_count==1
                assert store.pending_visual()
                claimed=store.claim_visual(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",limit=1,lease_seconds=30,
                )
                assert len(claimed)==1
                ticket=claimed[0]
                assert store.claim_visual(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",
                )==()
                fingerprint="a"*64
                assert store.record_visual_content(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",scope_id="fictional-guild",
                    kind="sticker",native_id="sticker-1234",
                    claim_token=ticket.claim_token,
                    expected_fingerprint=item.fingerprint,
                    content_fingerprint=fingerprint,
                    content_mime="image/png",content_size_bytes=1200,
                ) is True
                enriched=ExpressionSemanticProfile(
                    short_description="Coelho azul com surpresa",
                    visual_concepts=["coelho"],confidence=0.96,
                )
                assert store.apply_visual_enrichment(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",scope_id="fictional-guild",
                    kind="sticker",native_id="sticker-1234",
                    profile=enriched,expected_fingerprint=item.fingerprint,
                    claim_token=ticket.claim_token,
                    content_fingerprint=fingerprint,provider="synthetic",
                    model="fictional-model",
                ) is True
                assert store.pending_visual()==()
                hydrated=store.hydrate(message)
                assert hydrated.expressions[0].known_to_amora is True
                assert hydrated.expressions[0].semantic_profile is not None
                assert hydrated.expressions[0].semantic_revision==1
                assert store.apply_visual_enrichment(
                    platform=MessagingPlatform.DISCORD,
                    account_scope="primary",scope_id="fictional-guild",
                    kind="sticker",native_id="sticker-1234",
                    profile=enriched,expected_fingerprint=item.fingerprint,
                    claim_token=ticket.claim_token,
                    content_fingerprint=fingerprint,provider="synthetic",
                    model="fictional-model",
                ) is True
            with closing(AmoraDatabase(path,backend=backend)) as restarted:
                store=PerceptualExpressionMemory(
                    PostgresMessagingStore(restarted,clock),clock,
                )
                assert store.known_count()==1
                assert store.get(
                    platform=MessagingPlatform.DISCORD,account_scope="primary",
                    scope_id="fictional-guild",kind="sticker",
                    native_id="sticker-1234",
                ).semantic_revision==1
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
