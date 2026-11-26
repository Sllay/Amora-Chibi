"""RUN01 A: candidate extraction is not public delivery authorization."""
import json
from uuid import uuid4

import httpx
import pytest

from amora.cognition.realtime_candidates import (
    CANDIDATE_SINK, CandidateEmitter, CandidateError, MoveExtractor,
)
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.cognition.providers import ProviderError
from amora.messaging.realtime_contract import DeliverySnapshot, utf16_units
from test_reflection_provider_contract import fake_http


@pytest.mark.parametrize("chunk_size",[1,2,7,47,4096])
@pytest.mark.parametrize("moves_first",[True,False])
def test_incremental_completed_moves_unicode_and_field_order(chunk_size,moves_first):
    moves=[{"text":"Oi \"Amora\" 🐾\n{teste}","kind":"answer"},
           {"kind":"answer","text":"Mais informação."}]
    other={"self_choices":[{"key":"private","value":"NOT_SPEECH"}],"closing":"statement"}
    raw={"moves":moves,**other} if moves_first else {**other,"moves":moves}
    wire=json.dumps(raw,ensure_ascii=True)
    parser=MoveExtractor({"answer"})
    seen=[]
    for start in range(0,len(wire),chunk_size):
        seen.extend(parser.feed(wire[start:start+chunk_size]))
    parser.finish(raw)
    assert seen==[moves[0]["text"],moves[0]["text"]+" "+moves[1]["text"]]
    assert all("NOT_SPEECH" not in value for value in seen)


def test_no_candidate_until_move_and_delimiter_complete():
    parser=MoveExtractor({"answer"})
    assert parser.feed('{"moves":[{"text":"hello","kind":"answer"}')==()
    assert parser.feed(']')==("hello",)
    assert parser.feed('}')==()
    parser.finish({"moves":[{"text":"hello","kind":"answer"}]})


@pytest.mark.parametrize("wire",[
    '{"moves":[{"kind":"private","text":"secret"}]}',
    '{"moves":[{"kind":"answer","text":"a","text":"b"}]}',
    '{"moves":[{"kind":"answer","text":"a","private":"secret"}]}',
    '{"moves":[{"kind":"answer","text":"\\ud800"}]}',
    '{"moves":[{"kind":"answer","text":"SILENCE"}]}',
    '{"moves":[{"kind":"answer","text":"a"}], "moves":[]}',
    '{"moves":[{"kind":"answer","text":"a"}], "unknown":"secret"}',
    '{"moves":[{"kind":"answer","text":"a"},]}',
])
def test_bad_stream_invalidated(wire):
    seen=[]
    emitter=CandidateEmitter(seen.append,"attempt",{"answer"})
    emitter.feed(wire)
    emitter.close()
    assert seen[-1].status=="invalidated"
    assert "secret" not in "".join(event.text for event in seen)
    assert all("secret" not in repr(event) for event in seen)


def test_bounded_or_truncated_stream_invalidated():
    seen=[]
    emitter=CandidateEmitter(seen.append,"a",{"answer"})
    emitter.feed('{"moves":[{"kind":"answer","text":"a"},')
    assert seen[0].text=="a"
    emitter.close()
    assert [e.status for e in seen]==["candidate","invalidated"]
    assert seen[-1].text==""
    emitter.feed("x"*70000)
    assert len(seen)==2
    with pytest.raises(CandidateError):
        MoveExtractor({"answer"}).feed("x"*65537)


def test_cursor_only_in_transport_and_utf16_limit():
    ids=dict(turn_id=uuid4(),group_id=uuid4(),attempt_id=uuid4(),revision=1,seq=1)
    snapshot=DeliverySnapshot(**ids,status="writing",text="Olá 🐾")
    assert snapshot.discord_block()=="Olá 🐾 ▍"
    assert snapshot.text=="Olá 🐾"
    assert snapshot.model_copy(update={"status":"final"}).discord_block()=="Olá 🐾"
    assert snapshot.model_copy(update={"status":"interrupted"}).discord_block().endswith("[Resposta interrompida.]")
    assert utf16_units("🐾")==2
    exact=DeliverySnapshot(**ids,status="writing",text="🐾"*999)
    assert utf16_units(exact.discord_block())==2000
    with pytest.raises(ValueError,match="DISCORD_BLOCK_LIMIT"):
        DeliverySnapshot(**ids,status="writing",text="🐾"*1000).discord_block()


@pytest.mark.asyncio
@pytest.mark.parametrize("done",[True,False])
async def test_numes_sse_candidate_channel_is_separate_from_reasoning(monkeypatch,done):
    chunks=[
        {"choices":[{"delta":{"reasoning_content":"PRIVATE_REASONING"}}]},
        {"choices":[{"delta":{"content":'{"moves":[{"kind":"answer","text":"Olá"},'}}]},
        {"choices":[{"delta":{"content":'{"text":"Mundo","kind":"answer"}],"closing":"statement"}'},
                      "finish_reason":"stop"}]},
    ]
    body="".join("data: "+json.dumps(c)+"\n\n" for c in chunks)
    if done:
        body+="data: [DONE]\n\n"
    fake_http(monkeypatch,lambda req:httpx.Response(
        200,content=body.encode(),headers={"content-type":"text/event-stream"}))
    seen=[]
    token=CANDIDATE_SINK.set(seen.append)
    try:
        provider=NumesCognitiveProvider({"NUMES_API_KEY":"test"})
        if done:
            result=await provider._complete_stream({},key="test",phase="realization",candidate_moves=["answer"])
            assert result["moves"][1]["text"]=="Mundo"
        else:
            with pytest.raises(ProviderError):
                await provider._complete_stream({},key="test",phase="realization",candidate_moves=["answer"])
    finally:
        CANDIDATE_SINK.reset(token)
    assert [e.status for e in seen]==["candidate","candidate","transport_complete" if done else "invalidated"]
    assert [e.text for e in seen]==["Olá","Olá Mundo",""]
    assert [e.seq for e in seen]==[1,2,3]
    assert len({e.attempt_id for e in seen})==1
    assert "PRIVATE_REASONING" not in repr(seen)
