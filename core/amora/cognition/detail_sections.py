"""Bounded section generation on the existing provider/failover path.

A checkpoint is a private draft, not delivered conversation or canonical memory.
Resume only exact context and source revision under live transport authorization.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from time import perf_counter

from .models import JournalStage
from .realization import RealizationFrame, RealizationValidator, RealizationError, ValidatedRealization
from .surface_performance import detailed_output_requested, SYNC_REALIZATION_RETRY
from .durable_realization import DURABLE_REALIZATION, DURABLE_AUTHORIZATION, DeferredRealization

TTL = 900
VERSION = 2


def section_scopes(text):
    from .task_contract import requested_topics
    topics=requested_topics(text)
    if topics:
        middle=(len(topics)+1)//2
        return ("; ".join(topics[:middle]),"; ".join(topics[middle:]))
    # Keep exact request clauses; full literal remains in every provider input.
    clauses = [p.strip() for p in re.split(r"(?<=[.!?;])\s+|\n+",text) if p.strip()]
    substantive = [p for p in clauses if len(p)>=32]
    if len(substantive)<2:
        return ()
    middle = (len(substantive)+1)//2
    return (" ".join(substantive[:middle])," ".join(substantive[middle:]))


def create_detail_session(engine,request,context,on_segment):
    if (on_segment is not None or not detailed_output_requested(context)
            or request.metadata.get("surface")!="platform_messaging"
            or request.metadata.get("platform")!="discord"
            or request.metadata.get("channel_type")!="dm"
            or request.metadata.get("test_mode")
            or request.metadata.get("cognitive_mode","reply")!="reply"
            or not request.session_id or not request.metadata.get("native_message_id_hash")
            or type(request.metadata.get("transport_revision")) is not int
            or context.web_research or context.perceptual_expressions
            or context._compiler_overflow or context._adaptive_projection
            or context.dialogue_plan.get("self_choice_keys")
            or context.dialogue_plan.get("closing_mode")=="question"):
        return None
    durable = DURABLE_REALIZATION.get()==str(context.turn_id)
    sync = SYNC_REALIZATION_RETRY.get()
    guard = DURABLE_AUTHORIZATION.get() if durable else sync[1] if sync and sync[0]==str(context.turn_id) else None
    scopes = section_scopes(context.user_text)
    if guard is None or len(scopes)!=2:
        return None
    return DetailSession(engine,request,context,scopes,guard,durable)


class DetailSession:
    def __init__(self,engine,request,context,scopes,guard,durable):
        self.engine,self.request,self.context=engine,request,context
        self.scopes,self.guard,self.durable=scopes,guard,durable
        self.frames=[]
        self.providers=[]
        self.usage=[]
        self.usage_persistence=[]
        self.loaded=False
        snapshot=context.model_dump(mode="json",exclude={"created_at"})
        self.key=hashlib.sha256(json.dumps([VERSION,request.session_id,
            request.metadata["native_message_id_hash"],request.metadata["transport_revision"],
            snapshot,scopes],sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()

    async def authorize(self):
        if not await self.guard():
            if self.durable:
                raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
            from amora.messaging.generation_lifecycle import InboundGenerationLost
            raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")

    def validate(self,raw):
        frame=RealizationFrame.model_validate(raw)
        if len(frame.moves)>2 or frame.self_choices or frame.closing.value=="question":
            raise RealizationError("DETAIL_SECTION_INVALID","section contract mismatch")
        allowed=set(self.context.dialogue_plan.get("response_moves") or [])
        if not allowed or any(m.kind not in allowed or m.kind in {
                "ask_followup","ask_question","request_more","offer_help","offer_support",
                "invite_request","stay_silent"} for m in frame.moves):
            raise RealizationError("DETAIL_SECTION_INVALID","unauthorized section move")
        if not all(m.text.strip() for m in frame.moves):
            raise RealizationError("DETAIL_SECTION_INVALID","empty section")
        return frame

    async def load(self):
        if self.loaded:
            return
        await self.authorize()
        store=self.engine.store
        lane=store.async_lane
        rows=await lane.call("journal_for_turn",self.request.turn_id) if lane else store.journal_for_turn(self.request.turn_id)
        now=self.engine.clock.now()
        valid={}
        for row in rows:
            if (row.stage!=JournalStage.DETAIL_SECTION_CHECKPOINT
                    or row.privacy_scope!=self.context.privacy_scope
                    or not timedelta(0)<=now-row.timestamp<=timedelta(seconds=TTL)
                    or row.payload.get("key")!=self.key):
                continue
            index=row.payload.get("index")
            if type(index) is not int or not 0<=index<len(self.scopes):
                continue
            try:
                frame=self.validate(row.payload.get("frame"))
            except (ValueError,TypeError):
                continue
            if index in valid and valid[index][0]!=frame:
                # Ambiguous competing drafts: do not reconstruct a mixed reply.
                return
            valid[index]=(frame,row.provider,row.model)
        await self.authorize()
        for index in range(len(self.scopes)):
            if index not in valid:
                break
            frame,provider,model=valid[index]
            if self.repeated(frame):
                break
            self.frames.append(frame)
            self.providers.append((provider,model))
        self.loaded=True

    def repeated(self,frame):
        from .task_contract import repeated_passage
        if frame in self.frames or any(m.text.strip()==old.text.strip()
                for m in frame.moves for prev in self.frames for old in prev.moves):
            return True
        return repeated_passage(" ".join(m.text for m in frame.moves),
            [" ".join(m.text for m in prev.moves) for prev in self.frames])

    async def run(self,realize,descriptor):
        await self.load()
        while len(self.frames)<len(self.scopes):
            await self.authorize()
            index=len(self.frames)
            ctx=self.context.model_copy(deep=True)
            ctx._detail_section={"index":index+1,"count":len(self.scopes),
                "scope":self.scopes[index],
                "previous_scopes":list(self.scopes[:index])}
            started=perf_counter()
            result=dict(await realize(ctx))
            usage=result.pop("_usage",{})
            frame=self.validate(result)
            if self.repeated(frame):
                raise RealizationError("DETAIL_SECTION_REPEATED","repeated section")
            await self.authorize()
            bucket=int(self.engine.clock.now().timestamp())//TTL
            entry=self.engine._journal_entry(self.request,JournalStage.DETAIL_SECTION_CHECKPOINT,
                summary="private validated section draft, not delivered",
                reason_codes=["DETAIL_SECTION_SCHEMA_VALID"],
                provider=descriptor.name,model=descriptor.model,
                payload={"key":self.key,"index":index,"frame":frame.model_dump(mode="json")},
                dedupe_key=f"detail-section:v1:{self.key}:{index}:{bucket}")
            store=self.engine.store
            lane=store.async_lane
            saved=await lane.call("put_journal",entry) if lane else store.put_journal(entry)
            if not saved:
                raise RealizationError("DETAIL_CHECKPOINT_CONFLICT","checkpoint was not committed")
            self.frames.append(frame)
            self.providers.append((descriptor.name,descriptor.model))
            usage=usage if isinstance(usage,dict) else {}
            self.usage.append(usage)
            from amora.models import TaskClass
            from .provider_quota import ProviderQuotaDeferred
            def count(field):
                value=usage.get(field)
                return value if type(value) is int and value>=0 else None
            remaining_requests=count("remaining_requests")
            remaining_tokens=count("remaining_tokens")
            self.usage_persistence.append(self.engine._record_usage(
                provider=descriptor.name, model=descriptor.model or "unknown",
                task_class=TaskClass.SOCIAL_DIALOGUE.value, success=True,
                turn_id=self.request.turn_id,input_tokens=count("input_tokens"),
                output_tokens=count("output_tokens"),status_code=count("status_code"),
                latency_ms=(perf_counter()-started)*1000,
                remaining_requests=remaining_requests,remaining_tokens=remaining_tokens,
                metadata={"phase":"detail_section","section_index":index}))
            self.engine._provider_succeeded(descriptor,remaining_requests,remaining_tokens)
            if remaining_requests==0 or remaining_tokens==0:
                self.engine._provider_quota.block_for(descriptor.name,60,model=descriptor.model)
                if len(self.frames)<len(self.scopes):
                    raise ProviderQuotaDeferred(60)
            # Existing account admission stays held by the parent attempt.
            # No additional independent routing or retry loop.
        await self.authorize()
        combined=self.combined()
        totals={}
        for field in ("input_tokens","output_tokens"):
            vals=[u.get(field) for u in self.usage]
            if vals and all(type(v) is int and v>=0 for v in vals):
                totals[field]=sum(vals)
        return {**combined.model_dump(mode="json"),"_usage":totals}

    def combined(self,partial=False):
        moves=[m for frame in self.frames for m in frame.moves]
        if partial:
            from .realization import RealizationMove
            moves.append(RealizationMove(kind=moves[-1].kind,text=
                "A resposta ficou incompleta: não consegui desenvolver todas as seções solicitadas. "
                "O conteúdo acima é apenas a parte que consegui concluir."))
        return RealizationFrame(moves=moves,closing="statement")

    def partial(self):
        if not 0<len(self.frames)<len(self.scopes):
            return None
        frame=self.combined(partial=True)
        return ValidatedRealization(frame," ".join(m.text for m in frame.moves),
            ("DETAIL_SECTION_SCHEMA_VALID","DETAIL_RESPONSE_INCOMPLETE"))
