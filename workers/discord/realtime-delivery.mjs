// Realtime candidate transport. No gateway registration or live flag here.
// The caller must provide an authenticated, durable prepare/confirm journal
// and a fresh authorization guard. Never POST on an uncertain prior receipt.
import { createHash } from 'node:crypto';

const SNOWFLAKE = /^[0-9]{17,20}$/;
const TERMINAL = new Set(['final', 'interrupted']);
const DEFINITIVE = new Set([400, 401, 403, 404, 413, 415, 422, 429]);

function hash(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

export class RealtimeDelivery {
  constructor({ rest, journal, authorize, channelId, botUserId, binding, now=()=>performance.now() }) {
    if (!SNOWFLAKE.test(channelId) || !SNOWFLAKE.test(botUserId)
        || !/^[0-9a-f]{64}$/.test(binding)) throw new Error('REALTIME_BINDING_INVALID');
    this.rest=rest;
    this.journal=journal;
    this.authorize=authorize;
    this.channelId=channelId;
    this.botUserId=botUserId;
    this.binding=binding;
    this.now=now;
    this.inflight=false;
    this.lastSend=-Infinity;
    this.backoffUntil=0;
    this.lastText=new Map();
    this.lastSeq=new Map();
    this.maxBlocks=32;
    this.blocked=false;
  }

  async send(snapshot) {
    const {turnId,index,seq,status,text}=snapshot;
    if (typeof turnId!=='string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(turnId)
        || !Number.isSafeInteger(index) || index<0 || index>=this.maxBlocks
        || !Number.isSafeInteger(seq) || seq<1 || !['writing','retrying','final','interrupted'].includes(status)
        || typeof text!=='string' || !text.trim() || text.length>2000
        || (status==='writing' && !text.endsWith(' ▍'))
        || (status!=='writing' && text.endsWith(' ▍'))) throw new Error('REALTIME_SNAPSHOT_INVALID');
    if (this.blocked) return 'blocked';
    const key=turnId+':'+index;
    if (this.inflight || this.now()<this.backoffUntil) return 'deferred';
    if ((this.lastSeq.get(key)||0)>=seq) return 'stale';
    // The caller coalesces to the latest cumulative snapshot; no timer or
    // unbounded queue here. Final/interrupted flush skips the normal cadence.
    const previous=this.lastText.get(key)||'';
    if (!TERMINAL.has(status) && previous &&
        (this.now()-this.lastSend<1500 || (text.startsWith(previous.slice(0,-2)) && text.length-previous.length<24
            && this.now()-this.lastSend<3000))) return 'deferred';
    if (this.lastSeq.size>=this.maxBlocks && !this.lastSeq.has(key)) return 'capacity';
    this.inflight=true;
    try {
      if (!await this.authorize(snapshot)) return 'revoked';
      const digest=hash(text);
      const operation=await this.journal.prepare({turnId,index,binding:this.binding,seq,digest,
        terminal:TERMINAL.has(status)});
      if (!operation) return 'pending_or_stale';
      const token=operation.token;
      if (typeof token!=='string' || operation.seq!==seq || operation.digest!==digest
          || !['create','edit'].includes(operation.operation)
          || (operation.operation==='edit' ? !SNOWFLAKE.test(operation.message_id)
              : operation.message_id!==null)) {
        this.blocked=true;
        return 'journal_invalid';
      }
      const context={turnId,index,binding:this.binding,token};
      // No Discord request has happened yet, so a negative guard can safely
      // release this exact prepared operation.
      if (!await this.authorize(snapshot)) {
        await this.journal.rejectDefinitive(context);
        return 'revoked';
      }
      const edit=operation.operation==='edit';
      const base='/channels/'+encodeURIComponent(this.channelId)+'/messages';
      const path=edit?base+'/'+encodeURIComponent(operation.message_id):base;
      const body={content:text,allowed_mentions:{parse:[],replied_user:false}};
      if (!edit) {
        body.nonce=hash(turnId+':'+index).slice(0,24);
        body.enforce_nonce=true;
      }
      let receipt;
      try {
        receipt=await this.rest.request(path,{method:edit?'PATCH':'POST',body,maxAttempts:1,timeoutMs:10000});
      } catch(error) {
        const status=Number(error?.status);
        if (!DEFINITIVE.has(status)) {
          this.blocked=true;
          return 'receipt_uncertain';
        }
        if (!await this.journal.rejectDefinitive(context)) {
          this.blocked=true;
          return 'journal_uncertain';
        }
        if (status===429) {
          const retry=Number(error?.retryAfterMs);
          // Do not clip the provider's requested delay to a shorter limit.
          this.backoffUntil=this.now()+Math.max(1000,Number.isFinite(retry)?retry:30000);
          return 'rate_limited';
        }
        this.blocked=true;
        return 'rejected';
      }
      if (!SNOWFLAKE.test(String(receipt?.id||'')) || String(receipt?.channel_id)!==this.channelId
          || String(receipt?.author?.id)!==this.botUserId || receipt?.content!==text
          || (edit && String(receipt.id)!==operation.message_id)) {
        this.blocked=true;
        return 'receipt_uncertain';
      }
      if (!await this.journal.confirm({...context,messageId:String(receipt.id),digest})) {
        this.blocked=true;
        return 'journal_uncertain';
      }
      this.lastSend=this.now();
      this.lastText.set(key,text);
      this.lastSeq.set(key,seq);
      return 'confirmed';
    } catch {
      // A prepared operation might already exist. Never retry on exceptions
      // from the journal or an ambiguous external commit.
      this.blocked=true;
      return 'journal_uncertain';
    } finally {
      this.inflight=false;
    }
  }
}
