"""Field-regression policies; no external provider calls."""
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
import pytest

from amora.cognition.surface_performance import attempt_window, detailed_output_requested
from amora.cognition.providers_structured import _dialogue_token_budget
from amora.cognition.models import CognitiveContext
from amora.cognition.realization import realization_instruction
from amora.cognition.structured_runtime import _await_numes_adaptive
from amora.cognition.numes_stream_diagnostics import ACTIVE_NUMES_LIVENESS
from amora.events import PrivacyScope


def test_field_budget_keeps_failover_opportunity():
    assert attempt_window(90,3)==(60,30)
    assert attempt_window(90,1)==(78,12)
    assert attempt_window(30,2)==(19.5,10.5)
    assert attempt_window(10,0)==(10,0)
    assert attempt_window(0,4)==(0,0)


@pytest.mark.asyncio
async def test_live_progress_cannot_consume_reserved_fallback_window():
    cancelled=[]
    async def provider():
        watch=ACTIVE_NUMES_LIVENESS.get()
        watch.headers(200)
        try:
            while True:
                watch.keepalive()
                await asyncio.sleep(.005)
        finally:
            cancelled.append(True)
    window,reserve=attempt_window(.12,3)
    with pytest.raises(TimeoutError):
        await _await_numes_adaptive(provider(),timeout=.03,
            available_seconds=window,reserve_seconds=0,grace_cap_seconds=.5)
    assert cancelled==[True]
    assert reserve>0 and window+reserve==pytest.approx(.12)


def _context(text):
    return CognitiveContext(turn_id=uuid4(),created_at=datetime.now(timezone.utc),
        speaker_id="PERSON_BK",target_id="ENTITY_AMORA",user_text=text,
        privacy_scope=PrivacyScope.PERSONAL,remote_processing_allowed=True,
        dialogue_plan={"depth":"deep","response_moves":["answer_question"]})


def test_explicit_technical_document_budget_and_instruction():
    ctx=_context("Crie uma documentação técnica detalhada de um ecossistema. Explique cada sistema. Não resuma.")
    assert detailed_output_requested(ctx)
    assert _dialogue_token_budget(ctx)==2200
    assert "Não entregue apenas uma lista" in realization_instruction(ctx)
    assert "declare quais pontos ainda faltam" in realization_instruction(ctx)
    ordinary=_context("O que você acha de jogos de sobrevivência?")
    assert not detailed_output_requested(ordinary)
    assert _dialogue_token_budget(ordinary)==600
    assert "Não entregue apenas uma lista" not in realization_instruction(ordinary)
    assert not detailed_output_requested(ctx.model_copy(update={"remote_processing_allowed":False}))
    assert not detailed_output_requested(ctx.model_copy(update={"scene_visibility":"group"}))
    assert not detailed_output_requested(ctx.model_copy(update={"speaker_id":"OTHER"}))
    assert not detailed_output_requested(ctx.model_copy(update={"privacy_scope":PrivacyScope.PUBLIC}))


def test_empirical_rank_respects_preference_and_expiration():
    from types import SimpleNamespace
    from datetime import timedelta
    from amora.time import SimulatedClock
    from amora.cognition.surface_performance import ProviderPerformance
    from amora.cognition.provider_quota import ProviderQuotaCoordinator
    clock=SimulatedClock()
    performance=ProviderPerformance(clock)
    quota=ProviderQuotaCoordinator(clock)
    a=SimpleNamespace(name="a",model="a1",metadata={})
    b=SimpleNamespace(name="b",model="b1",metadata={})
    primary=SimpleNamespace(name="p",model="p1",metadata={"primary_social_preference":True})
    assert performance.rank((primary,a,b),"dialogue",quota=quota)==(primary,a,b)
    for _ in range(3):
        performance.observe(a,"dialogue",65,False)
        performance.observe(b,"dialogue",10,True)
        performance.observe(primary,"dialogue",90,False)
    assert performance.rank((primary,a,b),"dialogue",quota=quota)==(primary,b,a)
    assert performance.rank((primary,a,b),"detail",quota=quota)==(primary,a,b)
    clock.advance(timedelta(minutes=31))
    assert performance.rank((primary,a,b),"dialogue",quota=quota)==(primary,a,b)


def test_lossless_projection_reduces_identical_wire_data_without_changing_context(capsys):
    import json
    from amora.cognition.prompt_frame import render_trusted_protocol
    ctx=_context("Preserve literalmente: não remova a memória.")
    ctx.reason_codes=["MEMORY_EVIDENCE"]*30
    ctx.specialist_support={"note":"Evidência sintética. "*60}
    ctx.world_claims=[{"id":"evidence-a","claim":"texto","source":"A"}]*8+[{"id":"evidence-b","claim":"texto","source":"B"}]
    original=ctx.model_dump(mode="json")
    before=render_trusted_protocol(ctx)+"\n"+realization_instruction(ctx)
    after=render_trusted_protocol(ctx,compact_acir=True)+"\n"+realization_instruction(ctx,compact_support=True)
    assert len(after.encode())<len(before.encode())
    assert "evidence-a" in after and "evidence-b" in after
    assert after.count("Evidência sintética.")==60
    assert ctx.model_dump(mode="json")==original
    with capsys.disabled():
        print("RUN01_ENVELOPE_JSON="+json.dumps({"before_bytes":len(before.encode()),
             "after_bytes":len(after.encode()),"synthetic_duplicate_fixture":True}),flush=True)
