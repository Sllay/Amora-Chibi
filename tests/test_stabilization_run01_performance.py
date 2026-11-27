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


def test_normal_clock_projection_does_not_disable_explicit_technical_plan():
    from amora.cognition.dialogue import DialogueOrchestrator
    ctx=_context("Crie uma documentação técnica detalhada da arquitetura e do armazenamento. Explique cada sistema com seus algoritmos e restrições. Não resuma.")
    ctx.temporal_context={"current_time":"2026-11-25T19:00:00+00:00"}
    assert detailed_output_requested(ctx)
    plan=DialogueOrchestrator().plan(ctx)
    assert plan.closing_mode=="statement"
    assert not plan.ask_followup
    assert plan.depth.value=="deep"
    assert "EXPLICIT_TECHNICAL_DETAIL_REQUEST" in plan.reason_codes
    for code in ("COGNITIVE_REFLECTION","AUTONOMOUS_INITIATION","SCHEDULED_REMINDER"):
        assert not detailed_output_requested(ctx.model_copy(update={"reason_codes":[code]}))
    assert not detailed_output_requested(ctx.model_copy(update={"temporal_opportunity":{"kind":"initiative"}}))


def test_run02_b_rebudget_requires_material_reduction_and_preserves_evidence():
    from amora.cognition.context_budget import compact_rejected_payload
    from amora.cognition.models import ContextMemory
    ctx=_context("Explique o mecanismo e não remova as restrições.")
    ctx.specialist_support={"note":"x"*8000}
    ctx.web_research={"sources":[{"url":"https://example.invalid","text":"evidência "*120}]}
    ctx.memories=[ContextMemory(id="m",kind="semantic",content="relato original",score=1,
                               confidence=1,privacy_scope="PERSONAL")]
    def build(c):
        return {"user":c.user_text,"plan":c.dialogue_plan,"sources":c.web_research,
                "memories":[m.model_dump(mode="json") for m in c.memories],"support":c.specialist_support}
    original=build(ctx)
    reduced=compact_rejected_payload(ctx,build,original,min_reduction=.2,preserve_evidence=True)
    assert reduced is not None and reduced["support"] is None
    assert all(reduced[k]==original[k] for k in ("user","plan","sources","memories"))
    assert ctx.specialist_support is not None
    assert compact_rejected_payload(ctx,build,reduced,min_reduction=.2,preserve_evidence=True) is None
    ctx.specialist_support={"note":"x"*50}
    assert compact_rejected_payload(ctx,build,build(ctx),min_reduction=.2,preserve_evidence=True) is None


def test_run02_b_economic_continuity_and_explicit_coverage():
    from amora.cognition.task_contract import carried_constraints,task_guidance,quality_reasons,requested_topics
    from amora.cognition.prompt_frame import turn_frame
    from amora.cognition.detail_sections import section_scopes
    ctx=_context("Gostei, mas agora a aldeia B ficará impossibilitada de produzir por 30 dias. O comércio continua funcionando. Como resolvemos isso mantendo todas as regras que estabeleci anteriormente?")
    original=("A aldeia A só pode produzir madeira. A aldeia B só pode produzir pedra. "
              "A aldeia C só pode produzir alimentos. Nenhuma delas pode produzir recursos fora da sua especialidade, mesmo durante crises.")
    ctx.recent_messages=[{"speaker_id":"PERSON_BK","addressee_id":"ENTITY_AMORA","status":"received",
                          "turn_id":"prior","text":original}]
    assert len(carried_constraints(ctx))==4
    frame=turn_frame(ctx)["task_constraints"]
    assert len(frame["payload"])==4 and frame["prior_task_constraints_requested"]
    cue=task_guidance(ctx)
    assert "estoque final" in cue and "demanda acumulada" in cue and "transferências internas" in cue
    assert "TASK_ECONOMIC_VIABILITY_UNVERIFIED" in quality_reasons(ctx,"Reserve 30% de tudo.")
    assert "TASK_ECONOMIC_VIABILITY_TERMS_PRESENT" in quality_reasons(ctx,"Compare estoque e demanda durante 30 dias.")
    assert "TASK_SEMANTIC_COVERAGE_NOT_PROVEN" in quality_reasons(ctx,"Compare estoque e demanda durante 30 dias.")
    assert not carried_constraints(ctx.model_copy(update={"scene_visibility":"group"}))
    unrelated=ctx.model_copy(update={"recent_messages":[{"speaker_id":"PERSON_BK","addressee_id":"ENTITY_AMORA",
          "status":"received","turn_id":"other","text":"Não use Vulkan."}]})
    assert not carried_constraints(unrelated)
    text="Quero uma documentação técnica completa. Inclua arquitetura, algoritmos, alimentação, reprodução, migração, predadores, otimização, persistência e tratamento de falhas."
    topics=requested_topics(text)
    assert len(topics)==9
    scopes=section_scopes(text)
    assert len(scopes)==2 and all(any(topic in scope for scope in scopes) for topic in topics)
    doc=_context(text)
    assert "TASK_COVERAGE_LEXICAL_GAPS" in quality_reasons(doc,"Arquitetura do jogo.")


def test_run02_b_repeat_and_resource_delta():
    from amora.cognition.task_contract import repeated_passage
    from amora.resource_metrics import resource_delta
    sentence="A simulação mantém um estado determinístico separado do renderizador e executa os cálculos de cada região dentro de um orçamento explícito."
    assert repeated_passage("Nova seção. "+sentence,["Introdução. "+sentence])
    assert not repeated_passage("Nova seção de simulação.",["Introdução de simulação."])
    assert resource_delta({"process_cpu_ns":1000,"cgroup_throttled_usec":1200},
                          {"process_cpu_ns":1001000,"cgroup_throttled_usec":3200,
                           "cgroup_memory_bytes":512})=={
        "process_cpu_ms":1.0,"cgroup_throttled_ms":2.0,"cgroup_memory_end_bytes":512}
    assert resource_delta({"cgroup_nr_throttled":10},{"cgroup_nr_throttled":2})=={}


@pytest.mark.asyncio
@pytest.mark.parametrize("status",[429,503])
async def test_run02_b_interactive_rate_or_overload_goes_to_outer_controller(status):
    from types import SimpleNamespace
    from amora.cognition.surface_performance import INTERACTIVE_PROVIDER_ATTEMPT
    from amora.cognition.provider_resilience import ResilientProviderProxy
    from amora.cognition.providers import ProviderError
    calls=[]
    class Provider:
        descriptor=SimpleNamespace(name="fixture")
        async def realize(self,context):
            calls.append(context)
            raise ProviderError("synthetic",status_code=status,retry_after="1")
    context=_context("Pergunta direta.")
    token=INTERACTIVE_PROVIDER_ATTEMPT.set(True)
    try:
        with pytest.raises(ProviderError) as caught:
            await ResilientProviderProxy(Provider(),max_retries=3).realize(context)
        assert caught.value.status_code==status and calls==[context]
    finally:
        INTERACTIVE_PROVIDER_ATTEMPT.reset(token)


def test_run02_b_measured_reserve_remains_bounded():
    from types import SimpleNamespace
    from amora.time import SimulatedClock
    from amora.cognition.surface_performance import ProviderPerformance
    perf=ProviderPerformance(SimulatedClock())
    provider=SimpleNamespace(name="fixture",model="model")
    assert perf.fallback_reserve(provider,"dialogue")==12
    for _ in range(3):
        perf.observe(provider,"dialogue",3,True)
    assert perf.fallback_reserve(provider,"dialogue")==6
    assert attempt_window(90,1,measured_reserves=[6])==(84,6)
    assert attempt_window(30,2,measured_reserves=[18,18])==(19.5,10.5)
