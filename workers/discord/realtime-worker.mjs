// Explicit Realtime worker pilot. The Core is the sole source of snapshots;
// an uncertain receipt never falls back to ordinary POST.
import { createHash } from 'node:crypto';
import { RealtimeDelivery } from './realtime-delivery.mjs';
const hash=text=>createHash('sha256').update(text,'utf8').digest('hex');
const idOk=id=>/^[0-9]{17,20}$/.test(String(id||''));

export class RealtimeWorkerTransport {
  constructor({core,rest,botUserId,sourceCurrent,now}) {
    this.core=core;this.rest=rest;this.botUserId=botUserId;
    this.sourceCurrent=sourceCurrent;this.now=now;
    this.transports=new Map();
  }

  transport(envelope,turn,outboundId=null) {
    let transport=this.transports.get(turn);
    if (transport && (transport.channelId!==envelope.channelId || transport.binding!==envelope.binding))
      throw new Error('REALTIME_BINDING_CHANGED');
    const call=(action,x={})=>this.core.realtimeOperation({action,turn_id:turn,
      binding:envelope.binding,...(outboundId?{outbound_id:outboundId}:{}),
      ...(envelope.orphan?{orphan:true}:{}),...x});
    const journal={
      prepare:async x=>(await call('prepare',{index:x.index,seq:x.seq,digest:x.digest,terminal:x.terminal})).prepared,
      confirm:async x=>(await call('confirm',{index:x.index,token:x.token,message_id:x.messageId,digest:x.digest})).accepted===true,
      rejectDefinitive:async x=>(await call('reject',{index:x.index,token:x.token})).accepted===true,
    };
    const authorize=async s=>{
      if (s.status!=='retrying' && !this.sourceCurrent(envelope)) return false;
      const response=await call('authorize',{index:s.index,seq:s.seq,digest:hash(s.text),
        terminal:s.status==='final'||s.status==='interrupted'});
      return response?.allowed===true;
    };
    if (!transport || (transport.blocked && (outboundId || envelope.orphan))) {
      if (!transport && this.transports.size>=8) throw new Error('REALTIME_CAPACITY');
      transport=new RealtimeDelivery({rest:this.rest,journal,authorize,channelId:envelope.channelId,
        botUserId:this.botUserId,binding:envelope.binding,...(this.now?{now:this.now}:{})});
      this.transports.set(turn,transport);
    } else {
      transport.journal=journal;transport.authorize=authorize;
    }
    return transport;
  }

  async poll() {
    const {draft,orphans=[]}=await this.core.realtimeNext();
    if (draft) return this.transport(draft,draft.snapshot.turnId).send(draft.snapshot);
    // Only positive receipt reconciliation or neutral PATCH of a known ID.
    // Never create an orphan message. Bound each sweep and continue past
    // unresolved receipts instead of blocking unrelated turns.
    let result='idle';
    for (const orphan of orphans.slice(0,8)) {
      if (!orphan.orphan || !idOk(orphan.channelId)) continue;
      try {
        if (orphan.pending) {
          if (orphan.pending.turnId!==orphan.turnId || orphan.pending.channelId!==orphan.channelId ||
              orphan.pending.binding!==orphan.binding || orphan.pending.index!==0) continue;
          const accepted=await this.reconcilePending(orphan.pending);
          if (accepted) {
            this.transports.delete(orphan.turnId);
            result='recovered';
          } else result='receipt_uncertain';
        } else if (orphan.snapshot?.status==='retrying' && orphan.snapshot?.turnId===orphan.turnId &&
                   orphan.snapshot?.index===0 && orphan.snapshot?.text==='Geração interrompida.') {
          const outcome=await this.transport(orphan,orphan.turnId).send(orphan.snapshot);
          if (outcome==='confirmed') this.transports.delete(orphan.turnId);
          result=outcome;
        }
      } catch { result='receipt_uncertain'; }
    }
    return result;
  }

  async observeCreate(message) {
    if (String(message?.author?.id)!==this.botUserId || !idOk(message?.channel_id)
        || !idOk(message?.id) || typeof message?.content!=='string'
        || !/^r1[0-9a-f]{22}$/.test(String(message?.nonce||''))
        || typeof this.core.realtimeObserve!=='function') return false;
    const result=await this.core.realtimeObserve({channel_id:String(message.channel_id),
      message_id:String(message.id),nonce:String(message.nonce),digest:hash(message.content)});
    return result?.accepted===true;
  }

  async reconcilePending(p) {
    let receipt;
    if (idOk(p.messageId)) {
      receipt=await this.rest.request('/channels/'+encodeURIComponent(p.channelId)+
        '/messages/'+encodeURIComponent(p.messageId),{method:'GET',maxAttempts:1});
    } else {
      if (!/^r1[0-9a-f]{22}$/.test(String(p.nonce||'')) ||
          p.nonce!=='r1'+hash(p.token).slice(0,22)) return false;
      const recent=await this.rest.request('/channels/'+encodeURIComponent(p.channelId)+
        '/messages?limit=100',{method:'GET',maxAttempts:1});
      const matches=Array.isArray(recent)?recent.filter(m=>String(m?.nonce)===p.nonce
        && String(m?.author?.id)===this.botUserId && String(m?.channel_id)===p.channelId
        && idOk(m?.id) && typeof m?.content==='string' && hash(m.content)===p.digest):[];
      if (matches.length!==1) return false;
      receipt=matches[0];
    }
    if (!idOk(receipt?.id) || (p.messageId && String(receipt.id)!==p.messageId)
        || String(receipt?.channel_id)!==p.channelId
        || String(receipt?.author?.id)!==this.botUserId || typeof receipt.content!=='string'
        || hash(receipt.content)!==p.digest) return false;
    const confirmed=await this.core.realtimeOperation({action:'confirm',turn_id:p.turnId,
      binding:p.binding,index:p.index,token:p.token,digest:p.digest,message_id:String(receipt.id)});
    return confirmed?.accepted===true;
  }

  async final(item) {
    let plan=await this.core.realtimeFinal(item.id);
    if (plan?.blocked==='REALTIME_RECEIPT_UNCERTAIN' && plan.pending) {
      const p=plan.pending;
      if (p.channelId!==String(item.conversation_id) || p.turnId!==item.metadata?.cognitive_turn_id)
        return 'receipt_uncertain';
      if (!await this.reconcilePending(p)) return 'receipt_uncertain';
      plan=await this.core.realtimeFinal(item.id);
    }
    if (!plan || plan.blocked) return 'receipt_uncertain';
    if (plan.channelId!==String(item.conversation_id) || plan.sourceId!==String(item.source_message_id)
        || plan.turnId!==item.metadata?.cognitive_turn_id || !this.sourceCurrent(plan))
      return 'revoked';
    const t=this.transport({...plan,groupSources:item.metadata?.causal_group_sources},plan.turnId,item.id);
    for (const block of plan.blocks) {
      if (block.confirmedMessageId) continue;
      const outcome=await t.send(block.snapshot);
      if (outcome!=='confirmed') return outcome;
    }
    const complete=await this.core.realtimeFinal(item.id);
    if (complete?.blocked || !complete?.blocks?.length || complete.blocks.some(x=>!idOk(x.confirmedMessageId)))
      return 'receipt_uncertain';
    const ack=await this.core.realtimeOperation({action:'ack',turn_id:plan.turnId,
      binding:plan.binding,outbound_id:item.id,message_id:complete.blocks[0].confirmedMessageId});
    if (!ack?.accepted) return 'journal_uncertain';
    this.transports.delete(plan.turnId);
    return 'confirmed';
  }
}
