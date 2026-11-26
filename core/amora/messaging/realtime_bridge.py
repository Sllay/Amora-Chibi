"""Explicitly installed Realtime bridge for the authorized durable text pilot.

The receipt port is independently provisioned and serialized by the embedding
operator/lab. No default activation or schema migration. Candidate text is
ephemeral and never canonical. Canonical outbox is the authority for final text.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from time import monotonic
from uuid import UUID

from .delivery_segments import split_delivery_text
from .disclosure import public_text_allowed, audience_text_allowed
from .realtime_contract import utf16_units
from .realtime_receipts import binding_digest


def digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Draft:
    turn: str
    account: str
    channel: str
    source: str
    revision: int
    binding: str
    session: str
    guard: object = field(repr=False)
    seq: int = 0
    status: str = "writing"
    text: str = field(default="",repr=False)
    attempt: str | None = None
    active: bool = True
    touched: float = field(default_factory=monotonic)

    def pause(self):
        self.seq += 1
        self.status = "retrying"
        self.text = "Geração interrompida."
        self.touched = monotonic()

    def __call__(self, event):
        if not self.active:
            return
        self.touched = monotonic()
        if event.status == "invalidated":
            if self.attempt == event.attempt_id and self.text:
                self.pause()
            return
        if event.status != "candidate":
            return
        if self.attempt != event.attempt_id:
            self.attempt = event.attempt_id
        # Only complete schema-shaped moves. Never truncate an arbitrary
        # grapheme/Markdown fragment. The final outbox can contain more blocks.
        text = event.text
        if not text.strip() or utf16_units(text+" ▍") > 1970 or text.endswith(" ▍"):
            return
        if not public_text_allowed(text) or not audience_text_allowed(text,audience_id=self.session):
            self.pause()
            return
        self.seq += 1
        self.status = "writing"
        self.text = text+" ▍"

    def envelope(self):
        return {"account":self.account,"channelId":self.channel,"sourceId":self.source,
                "revision":self.revision,"binding":self.binding,
                "snapshot":{"turnId":self.turn,"index":0,"seq":self.seq,
                            "status":self.status,"text":self.text}}


class RealtimeBridge:
    def __init__(self,service,receipt_port):
        self.service=service
        self.receipts=receipt_port
        self.sessions={}

    @staticmethod
    def binding(message):
        try:
            return binding_digest(message.account_scope,message.conversation_id,
                                  message.native_source_message_id,message.transport_revision)
        except (TypeError,ValueError):
            return None

    async def begin(self,claim,message,request,guard):
        binding=self.binding(message)
        if binding is None:
            return None
        # Bound memory; never evict an active generation for a new draft.
        self.sessions={k:v for k,v in self.sessions.items() if v.active or monotonic()-v.touched<180}
        if len(self.sessions)>=8 and claim.turn_id not in self.sessions:
            return None
        row=await self.receipts("read",claim.turn_id,0)
        if row and (row["binding"]!=binding or row["closed"] or row["pending_token"]):
            return None
        draft=Draft(claim.turn_id,message.account_scope,message.conversation_id,
                    message.native_source_message_id,message.transport_revision,binding,
                    request.session_id,guard,seq=row["confirmed_seq"] if row else 0)
        self.sessions[claim.turn_id]=draft
        return draft

    def end(self,turn,*,completed):
        draft=self.sessions.get(turn)
        if draft:
            draft.active=False
            # Stop authorizing old text immediately. If the commit fails,
            # the neutral cleanup remains available for the known receipt.
            # If the final outbox wins, its terminal receipt closes cleanup.
            if draft.text:
                draft.pause()
            draft.touched=monotonic()

    async def _draft_current(self,draft,*,allow_reserved=False):
        if not draft.text:
            return False
        if draft.active and draft.status=="writing":
            seq=draft.seq
            if not await draft.guard():
                draft.active=False
                draft.pause()
                return False
            return (draft.active and seq==draft.seq and draft.status=="writing"
                    and public_text_allowed(draft.text)
                    and audience_text_allowed(draft.text,audience_id=draft.session))
        # Neutral cleanup only for a known message, never create a new one
        # after a failed/revoked attempt. No original text is exposed here.
        if draft.status=="retrying":
            row=await self.receipts("read",draft.turn,0)
            return bool(row and row["binding"]==draft.binding and row["message_id"]
                        and not row["closed"] and draft.seq>row["confirmed_seq"]
                        and (not row["pending_token"] or (allow_reserved
                            and row["pending_seq"]==draft.seq
                            and row["pending_digest"]==digest(draft.text)
                            and not row["pending_terminal"])))
        return False

    async def next(self,account):
        for draft in tuple(self.sessions.values()):
            if draft.account==account and await self._draft_current(draft):
                row=await self.receipts("read",draft.turn,0)
                if row and (row["pending_token"] or row["closed"] or row["confirmed_seq"]>=draft.seq):
                    continue
                if self.sessions.get(draft.turn) is draft:
                    return {"draft":draft.envelope()}
        return {"draft":None}

    def final_item(self,outbound_id,account):
        return self.service.store.realtime_final_item(outbound_id,account)

    @staticmethod
    def _final_binding(item):
        return binding_digest(item.account_scope,item.conversation_id,
                              item.source_message_id,int(item.metadata["transport_revision"]))

    async def final(self,outbound_id,account):
        item=self.final_item(outbound_id,account)
        if item is None:
            return None
        turn=str(UUID(item.metadata["cognitive_turn_id"]))
        binding=self._final_binding(item)
        chunks=split_delivery_text(item.text)
        if not chunks or len(chunks)>32:
            return {"blocked":"REALTIME_FINAL_SIZE"}
        snapshots=[]
        for i,text in enumerate(chunks):
            row=await self.receipts("read",turn,i)
            hashed=digest(text)
            if row and row["binding"]!=binding:
                return {"blocked":"REALTIME_BINDING_CONFLICT"}
            if row and row["closed"]:
                if row["confirmed_digest"]!=hashed or not row["message_id"]:
                    return {"blocked":"REALTIME_FINAL_RECEIPT_CONFLICT"}
                snapshots.append({"confirmedMessageId":row["message_id"]})
                continue
            if row and row["pending_token"]:
                # A known PATCH may be reconciled by GET; an unknown POST
                # remains blocked until an independent receipt is obtained.
                return {"blocked":"REALTIME_RECEIPT_UNCERTAIN",
                        "pending":{"turnId":turn,"index":i,"binding":binding,
                                   "token":row["pending_token"],"digest":row["pending_digest"],
                                   "messageId":row["message_id"],"channelId":item.conversation_id}}
            seq=(row["confirmed_seq"] if row else 0)+1
            snapshots.append({"snapshot":{"turnId":turn,"index":i,"seq":seq,
                                           "status":"final","text":text}})
        return {"account":account,"binding":binding,"channelId":item.conversation_id,
                "sourceId":item.source_message_id,"revision":item.metadata["transport_revision"],
                "turnId":turn,"blocks":snapshots}

    async def _expected(self,op):
        if op.outbound_id is not None:
            item=self.final_item(op.outbound_id,op.account)
            if item is None or self._final_binding(item)!=op.binding or str(item.metadata["cognitive_turn_id"])!=str(op.turn_id):
                return False
            chunks=split_delivery_text(item.text)
            if not op.terminal or not 0<=op.index<len(chunks) or digest(chunks[op.index])!=op.digest:
                return False
            row=await self.receipts("read",op.turn_id,op.index)
            if row and (row["binding"]!=op.binding or row["closed"]):
                return False
            return op.seq==(row["confirmed_seq"] if row else 0)+1
        draft=self.sessions.get(str(op.turn_id))
        if (draft is None or draft.account!=op.account or draft.binding!=op.binding
                or op.index!=0 or op.terminal or op.seq!=draft.seq or digest(draft.text)!=op.digest):
            return False
        if not await self._draft_current(draft,allow_reserved=op.action=="authorize"):
            return False
        return (self.sessions.get(draft.turn) is draft and op.seq==draft.seq
                and digest(draft.text)==op.digest)

    async def operation(self,op):
        if op.action in {"authorize","prepare"}:
            if not await self._expected(op):
                return {"allowed":False}
            if op.action=="authorize":
                return {"allowed":True}
            prepared=await self.receipts("prepare",op.turn_id,op.index,binding=op.binding,
                seq=op.seq,digest=op.digest,terminal=op.terminal)
            return {"prepared":asdict(prepared) if prepared else None}
        if op.action=="confirm":
            accepted=await self.receipts("confirm",op.turn_id,op.index,binding=op.binding,
                token=op.token,message_id=op.message_id,digest=op.digest)
            return {"accepted":accepted}
        if op.action=="reject":
            accepted=await self.receipts("reject_definitive",op.turn_id,op.index,
                binding=op.binding,token=op.token)
            return {"accepted":accepted}
        if op.action=="ack":
            plan=await self.final(op.outbound_id,op.account)
            if (not plan or "blocked" in plan or plan["binding"]!=op.binding
                    or plan["turnId"]!=str(op.turn_id)
                    or any(not x.get("confirmedMessageId") for x in plan["blocks"])
                    or plan["blocks"][0]["confirmedMessageId"]!=op.message_id):
                return {"accepted":False}
            accepted=self.service.store.ack_outbound(op.outbound_id,sent=True,
                                                    external_message_id=op.message_id)
            if accepted:
                self.sessions.pop(str(op.turn_id),None)
            return {"accepted":accepted}
        return {"accepted":False}
