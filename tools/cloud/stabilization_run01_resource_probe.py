"""Offline Core workload in a disposable resource-constrained container."""
from __future__ import annotations

import asyncio
import json
import math
import os
import resource
import statistics
import tempfile
import time
from pathlib import Path


def limits():
    # The runner must impose actual cgroup v2 ceilings, not just declare them.
    root=Path("/sys/fs/cgroup")
    quota,period=(root/"cpu.max").read_text().split()
    memory=int((root/"memory.max").read_text().strip())
    assert quota!="max" and 0<float(quota)/int(period)<=0.2
    assert 0<memory<=512*1024*1024
    return {"cpu_limit":float(quota)/int(period),"memory_limit_bytes":memory}


async def workload(folder):
    from amora.app import AmoraCore
    from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
    from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform
    from amora.events import PrivacyScope
    from amora.models import ProviderDescriptor, ProviderClass, TaskClass
    from amora.time import SimulatedClock

    os.environ.update(AMORA_HOME=folder,AMORA_MODE="local",
                      AMORA_STORAGE_BACKEND="sqlite",AMORA_DURABLE_REPLY_MODE="off")
    core=AmoraCore.bootstrap(clock=SimulatedClock())
    actor="111111111111111111"
    core.profiles.upsert_binding(PlatformIdentityBinding(
        platform=PlatformKind.DISCORD,identifier_type="discord_user_id",
        external_actor_id=actor,entity_id="PERSON_BK",account_scope="primary",
        trust=BindingTrust.OPERATOR_VERIFIED,confidence=1.0))
    class Fixture:
        descriptor=ProviderDescriptor(name="run01-offline-resource",model="synthetic",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,latency_score=1.0,cost_score=0.0,available=True,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=False,supports_structured_output=True,
            context_window_tokens=100000,priority=100)
        calls=0
        async def realize(self,context):
            self.calls+=1
            scope=context._detail_section
            text=(f"Seção técnica {scope['index']}: dados e algoritmos sintéticos."
                  if scope else "Resposta sintética estável.")
            return {"moves":[{"kind":context.dialogue_plan["response_moves"][0],"text":text}],
                    "closing":"statement","_usage":{"input_tokens":12,"output_tokens":8,"status_code":200}}
    provider=Fixture()
    core.cognition.register_provider(provider)
    times=[]
    details=0
    try:
        for i in range(14):
            is_detail=i in {4,11}
            text=("Crie uma documentação técnica detalhada da arquitetura e do armazenamento. "
                  "Explique cada sistema com seus algoritmos e restrições. Não resuma."
                  if is_detail else f"Explique um conceito de programação número {i}.")
            message=InboundMessage(platform=MessagingPlatform.DISCORD,account_scope="primary",
                conversation_id="run01-resource-dm",message_id=f"resource-{i}",
                native_message_id=f"resource-{i}",channel_type=ChannelType.DM,
                sender_identifiers=[ExternalActorRef(identifier_type="discord_user_id",external_actor_id=actor)],
                text=text,timestamp=core.clock.now(),remote_processing_allowed=True)
            started=time.perf_counter()
            result=await core.messaging.receive(message)
            times.append((time.perf_counter()-started)*1000)
            assert len(result.outbound)==1 and not result.outbound[0].metadata["fallback_used"]
            assert result.outbound[0].metadata["provider"]=="run01-offline-resource"
            if is_detail:
                assert "Seção técnica 1" in result.outbound[0].text
                assert "Seção técnica 2" in result.outbound[0].text
                details+=1
            claimed=core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
                account_scope="primary",limit=1)
            assert len(claimed)==1 and claimed[0].id==result.outbound[0].id
            assert core.messaging_store.ack_outbound(claimed[0].id,sent=True,
                external_message_id=f"synthetic-resource-{i}")
        assert provider.calls==16
        assert not core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary",limit=1)
    finally:
        await core.messaging.stop_inflight()
        await core.messaging.stop_storage_lane()
        core.close()
    return {"turns":len(times),"detail_turns":details,"provider_calls":provider.calls,
            "p50_ms":round(statistics.median(times),3),
            "p95_ms":round(sorted(times)[math.ceil(len(times)*.95)-1],3)}


def main():
    assert not any(k.startswith("AMORA_SECRET_") for k in os.environ)
    cap=limits()
    started=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="amora-run01-") as folder:
        result=asyncio.run(workload(folder))
    stats=resource.getrusage(resource.RUSAGE_SELF)
    peak=Path("/sys/fs/cgroup/memory.peak")
    summary={**cap,**result,"cpu_seconds":round(stats.ru_utime+stats.ru_stime,3),
             "wall_seconds":round(time.perf_counter()-started,3),
             "peak_rss_kib":stats.ru_maxrss,
             "cgroup_peak_bytes":int(peak.read_text()) if peak.exists() else None,
             "physical_provider":False,"physical_discord":False,"backend":"sqlite"}
    print("RUN01_RESOURCE_JSON="+json.dumps(summary,sort_keys=True),flush=True)


if __name__=="__main__":
    main()
