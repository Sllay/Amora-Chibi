"""Explicit additive receipt component. Runtime verification is read-only.

Requires a pinned PG18 target, existing certified durable reply schema and
separate backup/restore/maintenance approval. Never installs at startup.
"""
from contextlib import closing
import sqlite3
from amora.messaging.realtime_receipts import _DDL,PG_TABLE,SQLITE_TABLE
from .postgres_reply_job_migration import verify_target,review_reply_schema

APPROVAL="AUTHORIZE_VERIFIED_REALTIME_RECEIPTS_V1"
COMMENT="amora:realtime-receipts:v1"
_COLUMNS=(
    ("turn_id","text","NO"),("block_index","integer","NO"),("binding","text","NO"),
    ("confirmed_seq","integer","NO"),("message_id","text","YES"),
    ("confirmed_digest","text","YES"),("closed","integer","NO"),
    ("pending_token","text","YES"),("pending_seq","integer","YES"),
    ("pending_digest","text","YES"),("pending_terminal","integer","YES"),
)
_CHECKS={
    "CHECK (((block_index >= 0) AND (block_index <= 31)))",
    "CHECK ((confirmed_seq >= 0))",
    "CHECK ((closed = ANY (ARRAY[0, 1])))",
    "CHECK ((pending_terminal = ANY (ARRAY[0, 1])))",
    "CHECK ((((pending_token IS NULL) AND (pending_seq IS NULL) AND (pending_digest IS NULL) AND (pending_terminal IS NULL)) OR ((pending_token IS NOT NULL) AND (pending_seq > confirmed_seq) AND (pending_digest IS NOT NULL) AND (pending_terminal IS NOT NULL))))",
    "CHECK (((closed = 0) OR ((pending_token IS NULL) AND (message_id IS NOT NULL))))",
}


def verify_sqlite_receipts(connection):
    def definition(db):
        row=db.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='realtime_receipts'").fetchone()
        return row[0] if row else None
    with closing(sqlite3.connect(":memory:")) as ref:
        ref.execute(_DDL.format(table=SQLITE_TABLE))
        if definition(connection)!=definition(ref):
            raise ValueError("REALTIME_SQLITE_SCHEMA_NOT_PREPARED")
    return {"state":"prepared","version":1}


def review_realtime_schema(raw,target):
    verify_target(raw,target)
    with raw.transaction():
        if review_reply_schema(raw,target)["state"]!="prepared":
            raise ValueError("REALTIME_REPLY_SCHEMA_REQUIRED")
        if not raw.execute("SELECT to_regclass('amora_pg_v18_lab.realtime_receipts') IS NOT NULL").fetchone()[0]:
            return {"state":"absent","version":0}
        comment=raw.execute("SELECT obj_description('amora_pg_v18_lab.realtime_receipts'::regclass,'pg_class')").fetchone()[0]
        cols=tuple(raw.execute("""SELECT column_name,data_type,is_nullable FROM information_schema.columns
            WHERE table_schema='amora_pg_v18_lab' AND table_name='realtime_receipts' ORDER BY ordinal_position""").fetchall())
        constraints=raw.execute("""SELECT contype,pg_get_constraintdef(oid),convalidated,conenforced
            FROM pg_constraint WHERE conrelid='amora_pg_v18_lab.realtime_receipts'::regclass""").fetchall()
        checks=[" ".join(definition.split()) for kind,definition,*_ in constraints if kind=="c"]
        keys={definition for kind,definition,*_ in constraints if kind in {"p","u"}}
        defaults=dict(raw.execute("""SELECT column_name,column_default FROM information_schema.columns
            WHERE table_schema='amora_pg_v18_lab' AND table_name='realtime_receipts' AND column_default IS NOT NULL""").fetchall())
        if (comment!=COMMENT or cols!=_COLUMNS or defaults!={"confirmed_seq":"0","closed":"0"}
                or keys!={"PRIMARY KEY (turn_id, block_index)","UNIQUE (message_id)"}
                or len(checks)!=len(_CHECKS) or set(checks)!=_CHECKS
                or len(constraints)!=len(_CHECKS)+2
                or any(not valid or not enforced for _,_,valid,enforced in constraints)):
            raise ValueError("REALTIME_SCHEMA_STRUCTURAL_MISMATCH")
        return {"state":"prepared","version":1}


def prepare_realtime_schema(raw,target,*,approval):
    verify_target(raw,target)
    if approval!=APPROVAL:
        raise ValueError("REALTIME_SCHEMA_OPERATOR_APPROVAL_REQUIRED")
    with raw.transaction():
        raw.execute("SELECT pg_advisory_xact_lock(8147108342241062)")
        state=review_realtime_schema(raw,target)
        if state["state"]=="prepared":
            return state
        raw.execute(_DDL.format(table=PG_TABLE))
        raw.execute("COMMENT ON TABLE amora_pg_v18_lab.realtime_receipts IS 'amora:realtime-receipts:v1'")
        return review_realtime_schema(raw,target)
