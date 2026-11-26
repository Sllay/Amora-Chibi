import assert from 'node:assert/strict';
import test from 'node:test';
import { RealtimeDelivery } from '../workers/discord/realtime-delivery.mjs';

const turnId='00000000-0000-4000-8000-000000000001';
const channel='1'.repeat(18), bot='2'.repeat(18), id='3'.repeat(18);
function fixture(){
  let pending=null, receipt=null, seq=0, closed=false, now=1000, permitted=true;
  const calls=[];
  const journal={
    async prepare(x){
      if(pending||closed||x.seq<=seq)return null;
      pending={...x,token:'t'+x.seq};
      return {token:pending.token,seq:x.seq,digest:x.digest,operation:receipt?'edit':'create',message_id:receipt};
    },
    async confirm(x){
      if(pending?.token!==x.token||pending.digest!==x.digest)return false;
      if(receipt&&receipt!==x.messageId)return false;
      receipt=x.messageId;seq=pending.seq;closed=pending.terminal;pending=null;return true;
    },
    async rejectDefinitive(x){if(pending?.token!==x.token)return false;pending=null;return true;},
  };
  const rest={async request(path,opts){
    calls.push({path,opts});
    return {id,channel_id:channel,author:{id:bot},content:opts.body.content};
  }};
  const make=()=>new RealtimeDelivery({rest,journal,authorize:async()=>permitted,channelId:channel,
    botUserId:bot,binding:'a'.repeat(64),now:()=>now});
  return {journal,rest,make,calls,advance:ms=>now+=ms,deny:()=>permitted=false};
}
function snap(seq,text,status='writing'){return {turnId,index:0,seq,status,text};}

test('one POST then PATCH, cursor only during writing, no pings',async()=>{
  const f=fixture(), d=f.make();
  assert.equal(await d.send(snap(1,'Primeiro trecho. ▍')),'confirmed');
  assert.equal(await d.send(snap(2,'Primeiro trecho. Mais texto. ▍')),'deferred');
  f.advance(3000);
  assert.equal(await d.send(snap(2,'Primeiro trecho. Mais texto. ▍')),'confirmed');
  assert.equal(await d.send(snap(3,'Resposta final.','final')),'confirmed');
  assert.deepEqual(f.calls.map(c=>c.opts.method),['POST','PATCH','PATCH']);
  assert.equal(f.calls[1].path,'/channels/'+channel+'/messages/'+id);
  assert.equal(f.calls[2].opts.body.content,'Resposta final.');
  assert.deepEqual(f.calls[0].opts.body.allowed_mentions,{parse:[],replied_user:false});
  assert.ok(f.calls[0].opts.body.enforce_nonce);
  assert.equal(f.calls[0].opts.maxAttempts,1);
  assert.equal(await d.send(snap(3,'Resposta final.','final')),'stale');
});

test('uncertain POST stays pending across replacement worker, no blind resend',async()=>{
  const f=fixture(),d=f.make();
  f.rest.request=async()=>{f.calls.push('POST');throw new Error('timeout');};
  assert.equal(await d.send(snap(1,'Trecho. ▍')),'receipt_uncertain');
  assert.equal(await f.make().send(snap(2,'Novo trecho. ▍')),'pending_or_stale');
  assert.equal(f.calls.length,1);
});

test('rate limit honored, even long retry; final does not bypass',async()=>{
  const f=fixture(),d=f.make();
  const request=f.rest.request;
  f.rest.request=async()=>{throw Object.assign(new Error('429'),{status:429,retryAfterMs:45000});};
  assert.equal(await d.send(snap(1,'Trecho. ▍')),'rate_limited');
  f.rest.request=request;
  f.advance(30001);
  assert.equal(await d.send(snap(2,'Final.','final')),'deferred');
  f.advance(15000);
  assert.equal(await d.send(snap(2,'Final.','final')),'confirmed');
  assert.equal(f.calls.length,1);
});

test('revocation and invalid receipt fail closed',async()=>{
  const f=fixture(),d=f.make();
  f.deny();
  assert.equal(await d.send(snap(1,'Trecho. ▍')),'revoked');
  assert.equal(f.calls.length,0);
  const other=fixture();
  other.rest.request=async()=>({id,channel_id:'9'.repeat(18),author:{id:bot},content:'Trecho. ▍'});
  assert.equal(await other.make().send(snap(1,'Trecho. ▍')),'receipt_uncertain');
  assert.equal(await other.make().send(snap(2,'Final.','final')),'pending_or_stale');
});

test('source revoked after reservation never triggers POST',async()=>{
  const f=fixture();
  const prepare=f.journal.prepare;
  f.journal.prepare=async x=>{const value=await prepare(x);f.deny();return value;};
  assert.equal(await f.make().send(snap(1,'Trecho. ▍')),'revoked');
  assert.equal(f.calls.length,0);
});


test('worker bridge previews and reconciles final into same message',async()=>{
  const {RealtimeWorkerTransport}=await import('../workers/discord/realtime-worker.mjs');
  const {createHash}=await import('node:crypto');
  const digest=x=>createHash('sha256').update(x).digest('hex');
  const turn=turnId,binding='b'.repeat(64);
  let final=false,pending=null,confirmed=false,acked=false,receipt=null,seq=0;
  const calls=[];
  const envelope={channelId:channel,sourceId:'4'.repeat(18),revision:1,binding,turnId:turn};
  const draft={...envelope,snapshot:{turnId:turn,index:0,seq:1,status:'writing',text:'Trecho. ▍'}};
  const last={turnId:turn,index:0,seq:2,status:'final',text:'Resposta completa.'};
  const core={
    async realtimeNext(){return {draft:final?null:draft};},
    async realtimeFinal(){return {...envelope,blocks:confirmed?[{confirmedMessageId:id}]:[{snapshot:last}]};},
    async realtimeOperation(op){
      if(op.action==='authorize')return {allowed:true};
      if(op.action==='prepare'){
        assert.ok(!pending);pending=op;return {prepared:{token:'token',seq:op.seq,digest:op.digest,
          operation:receipt?'edit':'create',message_id:receipt}};
      }
      if(op.action==='confirm'){
        assert.equal(op.digest,pending.digest);receipt=op.message_id;seq=pending.seq;
        confirmed=pending.terminal;pending=null;return {accepted:true};
      }
      if(op.action==='ack'){assert.ok(confirmed);acked=true;return {accepted:true};}
      throw new Error('unexpected');
    },
  };
  const rest={async request(path,opts){
    calls.push({path,opts});
    assert.equal(digest(opts.body.content),pending.digest);
    return {id,channel_id:channel,author:{id:bot},content:opts.body.content};
  }};
  const transport=new RealtimeWorkerTransport({core,rest,botUserId:bot,sourceCurrent:()=>true});
  assert.equal(await transport.poll(),'confirmed');
  final=true;
  const item={id:'outbound',conversation_id:channel,source_message_id:envelope.sourceId,
    metadata:{cognitive_turn_id:turn}};
  assert.equal(await transport.final(item),'confirmed');
  assert.ok(acked);assert.equal(seq,2);
  assert.deepEqual(calls.map(c=>c.opts.method),['POST','PATCH']);
  assert.equal(calls[1].opts.body.content,'Resposta completa.');
});
