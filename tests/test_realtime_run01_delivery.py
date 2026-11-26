"""RUN01 candidate projection, shadow metrics and durable receipt lab."""
from contextlib import closing
from hashlib import sha256
import os
import sqlite3
from uuid import uuid4

import pytest

from amora.cognition.realtime_candidates import CandidateEvent
from amora.messaging.realtime_contract import DeliverySnapshot, utf16_units
from amora.messaging.realtime_projection import CandidateAudit, project_blocks
from amora.messaging.realtime_receipts import RealtimeReceiptStore, binding_digest


def snapshot(text, status="writing"):
    return DeliverySnapshot(turn_id=uuid4(),group_id=uuid4(),attempt_id=uuid4(),
                            revision=1,seq=1,status=status,text=text)


def test_projection_cursor_stable_boundaries_and_no_text_loss():
    text="🐾"*700+"\n\n"+"a"*1400+"\n\n"+"b"*700
    s=snapshot(text)
    blocks=project_blocks(s)
    assert len(blocks)==3
    assert all(utf16_units(b.text)<=2000 for b in blocks)
    assert all(b.sealed and not b.text.endswith(" ▍") for b in blocks[:-1])
    assert blocks[-1].text.endswith(" ▍") and not blocks[-1].sealed
    final=project_blocks(s.model_copy(update={"status":"final"}))
    assert "".join(b.text for b in final)==text
    assert [b.text for b in blocks[:-1]]==[b.text for b in final[:-1]]
    assert all(b.sealed for b in final)


def test_projection_fails_closed_if_no_safe_break_or_markdown():
    with pytest.raises(ValueError,match="NO_SAFE_SPLIT"):
        project_blocks(snapshot("🐾"*1100))
    with pytest.raises(ValueError,match="MARKDOWN_SPLIT"):
        project_blocks(snapshot("code \x60\x60\x60\n"+"x"*2200+"\n\x60\x60\x60"))


def test_shadow_metrics_never_hold_text():
    a=CandidateAudit()
    a(CandidateEvent("a",1,"candidate","SECRET"))
    a(CandidateEvent("a",2,"invalidated"))
    a(CandidateEvent("b",1,"candidate","PRIVATE"))
    a(CandidateEvent("b",2,"transport_complete"))
    a(CandidateEvent("b",4,"candidate","BAD"))
    m=a.finish()
    assert m["attempts"]==2 and m["candidate_events"]==2
    assert m["invalidated_attempts"]==m["transport_completed"]==m["protocol_errors"]==1
    assert m["first_candidate_ms"]>=0
    assert "SECRET" not in repr(vars(a)) and "PRIVATE" not in repr(vars(a))
    a(CandidateEvent("c",1,"candidate","AFTER"))
    assert a.finish()==m


@pytest.fixture(params=["sqlite",pytest.param("pg18",marks=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",reason="Disposable PG18 required"))])
def store(request,tmp_path):
    if request.param=="sqlite":
        path=tmp_path/"realtime.sqlite3"
        def connect():
            return sqlite3.connect(path,timeout=5)
        with closing(connect()) as c:
            journal=RealtimeReceiptStore(c)
            journal.install_disposable()
            yield journal,lambda conn:RealtimeReceiptStore(conn),connect
        return
    psycopg=pytest.importorskip("psycopg")
    def connect():
        return psycopg.connect(host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
                               user="amora_ci",password="synthetic_only",autocommit=True)
    with closing(connect()) as c:
        # Only the explicit disposable local lab. Never select from env DSNs.
        assert c.execute("SHOW server_version_num").fetchone()[0].startswith("18")
        c.execute("CREATE SCHEMA IF NOT EXISTS amora_pg_v18_lab")
        c.execute("DROP TABLE IF EXISTS amora_pg_v18_lab.realtime_receipts")
        try:
            journal=RealtimeReceiptStore(c,postgres=True)
            journal.install_disposable()
            yield journal,lambda conn:RealtimeReceiptStore(conn,postgres=True),connect
        finally:
            c.execute("DROP TABLE IF EXISTS amora_pg_v18_lab.realtime_receipts")


def test_receipt_crash_reopen_and_uncertain_post_never_repeated(store):
    journal,factory,connect=store
    turn=uuid4()
    binding=binding_digest("primary","1"*18,"2"*18,1)
    digest=sha256(b"fixture draft").hexdigest()
    args=dict(binding=binding,seq=1,digest=digest)
    first=journal.prepare(turn,0,**args)
    assert first.operation=="create" and first.message_id is None
    assert journal.prepare(turn,0,**args) is None
    with closing(connect()) as c:
        reopened=factory(c)
        # Simulate crash after POST but before ACK; never issue another POST.
        assert reopened.prepare(turn,0,binding=binding,seq=2,digest=digest) is None
        assert reopened.read(turn,0)["pending_token"]==first.token
        assert not reopened.confirm(turn,0,binding=binding,token="wrong",message_id="3"*18,digest=digest)
        # Only an externally reconciled, verified receipt closes ambiguity.
        assert reopened.confirm(turn,0,binding=binding,token=first.token,message_id="3"*18,digest=digest)
        assert reopened.prepare(turn,0,**args) is None
        next_op=reopened.prepare(turn,0,binding=binding,seq=2,digest=digest,terminal=True)
        assert next_op.operation=="edit" and next_op.message_id=="3"*18
        assert not reopened.confirm(turn,0,binding=binding,token=next_op.token,message_id="4"*18,digest=digest)
        assert reopened.confirm(turn,0,binding=binding,token=next_op.token,message_id="3"*18,digest=digest)
        assert reopened.read(turn,0)["closed"]==1
        assert reopened.prepare(turn,0,binding=binding,seq=3,digest=digest) is None


def test_binding_fence_and_definitive_rejection(store):
    journal,_,_=store
    turn=uuid4()
    binding=binding_digest("primary","1"*18,"2"*18,1)
    other=binding_digest("primary","1"*18,"2"*18,2)
    digest=sha256(b"fixture").hexdigest()
    first=journal.prepare(turn,0,binding=binding,seq=1,digest=digest)
    assert not journal.reject_definitive(turn,0,binding=other,token=first.token)
    assert journal.prepare(turn,0,binding=other,seq=2,digest=digest) is None
    assert journal.reject_definitive(turn,0,binding=binding,token=first.token)
    second=journal.prepare(turn,0,binding=binding,seq=2,digest=digest)
    assert second.token!=first.token
    assert not journal.reject_definitive(turn,0,binding=binding,token=first.token)
    assert not journal.confirm(turn,0,binding=binding,token=second.token,message_id="3"*18,digest="0"*64)
    assert journal.read(turn,0)["pending_token"]==second.token
