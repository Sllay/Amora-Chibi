import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserState, PAIRING_KEY, HISTORY_KEY } from '../web/browser-state.mjs';
import { app, storage, token } from './helpers/web-app.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
const display = 'kkkk 😂, pai... **Tu é impossível!**';
function item(ordinal, turn = 'cognitive-turn-a') {
  return { id: `${turn}-${ordinal}`, text: `Fala ${ordinal}.`, display_text: display, privacy_scope: 'PERSONAL',
    metadata: { cognitive_turn_id: turn, cognitive_ordinal: ordinal } };
}
function paired({ muted = false } = {}) {
  const local = storage(); const state = new BrowserState(local);
  state.savePairing(token); state.setVoiceMuted(muted); return local;
}

test('three vocal segments produce one literal turn bubble/history entry; reclaim/reopen do not duplicate', async () => {
  const local = paired({ muted: true }); const b = await app(local);
  for (let n = 0; n < 3; n++) b.context.queueLocalSpeech(item(n));
  await tick();
  assert.deepEqual(b.messages().filter(m => m.role === 'amora'), [{ role: 'amora', text: display }]);
  assert.deepEqual(new BrowserState(local).messages, [{ role: 'amora', text: display, id: 'cognitive-turn-a' }]);
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 0);
  const reopened = await app(local);
  for (let n = 0; n < 3; n++) reopened.context.queueLocalSpeech(item(n));
  await tick();
  assert.equal(reopened.messages().filter(m => m.role === 'amora').length, 1);
  assert.equal(new BrowserState(local).messages.length, 1);
  assert.equal(reopened.contexts.length, 0);
});

test('50+50 limits apply to turns in live DOM and history, never their three segments', async () => {
  const local = paired({ muted: true }); const b = await app(local);
  for (let turn = 0; turn < 51; turn++) {
    b.context.addMessage('user', `entrada ${turn}`);
    for (let n = 0; n < 3; n++) b.context.queueLocalSpeech({ ...item(n, `turn-${turn}`), display_text: `resposta ${turn}` });
  }
  await tick();
  const messages = new BrowserState(local).messages;
  assert.equal(messages.length, 100);
  assert.equal(messages.filter(m => m.role === 'user').length, 50);
  assert.equal(messages.filter(m => m.role === 'amora').length, 50);
  assert.equal(messages[0].text, 'entrada 1');
  assert.equal(messages[1].text, 'resposta 1');
  assert.equal(b.messages().filter(m => ['user', 'amora'].includes(m.role)).length, 100);
});

test('clear requires accessible confirmation; cancel preserves everything; confirm affects only visual history', async () => {
  const local = paired({ muted: true }); const b = await app(local);
  b.context.addMessage('user', 'oi'); b.context.queueLocalSpeech(item(0));
  await tick();
  const before = local.getItem(HISTORY_KEY);
  b.nodes.get('clearConversation').listeners.click();
  assert.equal(b.nodes.get('clearDialog').open, true);
  assert.equal(local.getItem(HISTORY_KEY), before);
  b.nodes.get('cancelClear').listeners.click();
  assert.equal(b.nodes.get('clearDialog').open, false);
  assert.equal(local.getItem(HISTORY_KEY), before);
  assert.equal(b.messages().filter(m => ['user', 'amora'].includes(m.role)).length, 2);
  const calls = b.calls.length;
  b.nodes.get('clearConversation').listeners.click();
  b.nodes.get('confirmClear').listeners.click();
  assert.equal(local.getItem(HISTORY_KEY), null);
  assert.equal(local.getItem(PAIRING_KEY), token);
  assert.equal(new BrowserState(local).voiceMuted(), true);
  assert.equal(b.messages().length, 0);
  assert.equal(b.calls.length, calls);
  b.context.queueLocalSpeech(item(1)); // Old queued segment cannot resurrect cleared text.
  await tick();
  assert.equal(b.messages().length, 0);
});

test('mute persists across reopen, shows/saves responses, skips TTS and playback AudioContext, and settles leases', async () => {
  const local = paired(); const b = await app(local);
  await b.nodes.get('muteVoice').listeners.click();
  assert.equal(b.nodes.get('muteVoice').attributes['aria-pressed'], 'true');
  assert.match(b.nodes.get('muteVoice').textContent, /mutada/);
  assert.equal(b.contexts.length, 0);
  const reopened = await app(local);
  await reopened.context.submitText('oi');
  reopened.context.queueLocalSpeech(item(0));
  await tick();
  assert.equal(reopened.contexts.length, 0);
  assert.equal(reopened.calls.filter(c => c.path.endsWith('/audio')).length, 0);
  assert.equal(reopened.state.speechQueue.length, 0);
  const cancellation = reopened.calls.find(c => c.path.endsWith('/cancel'));
  assert.equal(JSON.parse(cancellation.request.body).speech_only, true);
  assert.equal(new BrowserState(local).messages.length, 2);
  assert.equal(new BrowserState(local).voiceMuted(), true);
});

test('mute drains actual claim loop without holding voice queue or creating audio contexts', async () => {
  const queue = [item(0), item(1), item(2)];
  const b = await app(paired({ muted: true }), { fetch: path => path.startsWith('/v1/conversation/next')
    ? Response.json({ utterance: queue.shift() || null }) : undefined });
  await tick();
  assert.equal(queue.length, 0);
  assert.equal(b.state.claimed.size, 0);
  assert.equal(b.state.speechQueue.length, 0);
  assert.equal(b.contexts.length, 0);
  assert.equal(b.messages().filter(m => m.role === 'amora').length, 1);
  assert.equal(b.calls.filter(c => c.path.endsWith('/cancel')).length, 3);
});

test('stop immediately halts player, aborts three fetches, cancels all claimed segments, and suppresses the turn', async () => {
  const b = await app(paired(), { fetch: path => path.endsWith('/audio') ? new Response(new Uint8Array([0, 1])) : undefined });
  await b.context.unlockPlayback();
  for (let n = 0; n < 3; n++) b.context.queueLocalSpeech(item(n));
  await tick();
  assert.equal(b.state.speechQueue.length, 3);
  assert.equal(b.sources.length, 1);
  const ended = b.sources[0].onended;
  const epoch = b.state.speechEpoch;
  await b.nodes.get('stopVoice').listeners.click();
  assert.ok(b.sources[0].stopped);
  assert.ok(b.state.speechEpoch > epoch);
  assert.equal(b.state.speechQueue.length, 0);
  assert.equal(b.state.claimed.size, 0);
  assert.equal(b.calls.filter(c => c.path.endsWith('/cancel')).length, 3);
  assert.ok(b.calls.filter(c => c.path.endsWith('/audio')).every(c => c.request.signal.aborted));
  ended();
  b.context.queueLocalSpeech(item(3)); // Late producer/reclaim cannot restart sound.
  await tick();
  b.context.pumpPlayback();
  assert.equal(b.sources.length, 1);
  assert.equal(b.calls.filter(c => c.path.endsWith('/ack')).length, 0);
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 3);
  assert.deepEqual(b.messages().filter(m => m.role === 'amora'), [{ role: 'amora', text: display }]);
});

test('stop while claim is in flight settles the late claim without starting TTS or losing text', async () => {
  const b = await app(paired());
  let resolve;
  b.context.claimNext = () => new Promise(done => { resolve = done; });
  const filling = b.context.fillSpeechQueue();
  await b.nodes.get('stopVoice').listeners.click();
  resolve(item(0));
  await filling; await tick();
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 0);
  assert.equal(b.state.claimed.size, 0);
  assert.equal(b.messages().find(m => m.role === 'amora').text, display);
});

test('look-ahead respects the Core single-render slot while keeping three claimed segments cancellable', async () => {
  let release;
  const b = await app(paired(), { fetch: (path, request) => path.endsWith('/audio')
    ? new Promise((resolve, reject) => {
      release = () => resolve(new Response(new Uint8Array([0, 1])));
      request.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    }) : undefined });
  await b.context.unlockPlayback();
  for (let n = 0; n < 3; n++) b.context.queueLocalSpeech(item(n));
  await tick();
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 1);
  release(); await tick();
  assert.equal(b.sources.length, 1); // Segment 1 plays while segment 2 renders.
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 2);
  await b.nodes.get('stopVoice').listeners.click();
  await tick();
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 2);
  assert.equal(b.calls.filter(c => c.path.endsWith('/cancel')).length, 3);
  assert.equal(b.state.preparingRecord, null);
  assert.equal(b.state.speechQueue.length, 0);
});

test('unmuting does not resume a suppressed turn and permits voice for a new turn', async () => {
  const local = paired({ muted: true });
  const b = await app(local, { fetch: path => path.endsWith('/audio') ? new Response(new Uint8Array([1])) : undefined });
  b.context.queueLocalSpeech(item(0)); await tick();
  await b.nodes.get('muteVoice').listeners.click();
  b.context.queueLocalSpeech(item(1));
  b.context.queueLocalSpeech(item(0, 'next-turn')); await tick();
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 1);
  assert.equal(b.sources.length, 1);
  assert.equal(new BrowserState(local).voiceMuted(), false);
  assert.equal(b.messages().filter(m => m.role === 'amora').length, 2);
});

test('late decode after stop cannot play or ACK; text remains', async () => {
  let resolve;
  const b = await app(paired(), { fetch: path => path.endsWith('/audio') ? new Response(new Uint8Array([1])) : undefined,
    decode: () => new Promise(done => { resolve = done; }) });
  await b.context.unlockPlayback();
  b.context.queueLocalSpeech(item(0));
  await tick();
  await b.nodes.get('stopVoice').listeners.click();
  resolve({ duration: 0.1 });
  await tick();
  assert.equal(b.sources.length, 0);
  assert.equal(b.calls.filter(c => c.path.endsWith('/ack')).length, 0);
  assert.equal(b.messages().find(m => m.role === 'amora').text, display);
});

test('pagehide releases the whole local playback lifecycle for Safari back-forward cache', async () => {
  const b = await app(paired(), { fetch: path => path.endsWith('/audio') ? new Response(new Uint8Array([0, 1])) : undefined });
  await b.context.unlockPlayback();
  for (let n = 0; n < 3; n++) b.context.queueLocalSpeech(item(n));
  await tick();
  b.windowListeners.pagehide();
  await tick();
  assert.equal(b.state.speechQueue.length, 0);
  assert.equal(b.state.activeRecord, null);
  assert.ok(b.sources.every(source => source.stopped));
  assert.equal(b.messages().find(m => m.role === 'amora').text, display);
});

test('cue-only response is displayed and settled without TTS', async () => {
  const b = await app(paired());
  b.context.queueLocalSpeech({ ...item(0), text: '', display_text: '😂' });
  await tick();
  assert.equal(b.messages().find(m => m.role === 'amora').text, '😂');
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 0);
  assert.equal(b.state.claimed.size, 0);
});

test('a new user utterance keeps the existing cognitive barge-in contract', async () => {
  const b = await app(paired(), { fetch: (path, request) => path.endsWith('/audio')
    ? new Promise((resolve, reject) => request.signal.addEventListener('abort',
      () => reject(new DOMException('aborted', 'AbortError')), { once: true })) : undefined });
  b.context.queueLocalSpeech(item(0));
  await b.context.submitText('nova fala');
  const cancel = b.calls.find(c => c.path.endsWith('/cancel'));
  assert.equal(JSON.parse(cancel.request.body).speech_only, false);
  assert.equal(b.messages().filter(m => m.role === 'amora').length, 1);
});

test('failed voice cancellation is diagnosed and retained for polling recovery', async () => {
  let failing = true;
  const b = await app(paired({ muted: true }), { fetch: path => path.endsWith('/cancel')
    ? Response.json({}, { status: failing ? 500 : 200 }) : undefined });
  b.context.queueLocalSpeech(item(0));
  await tick();
  assert.match(b.nodes.get('diagVoice').textContent, /cancelamento vocal pendente: HTTP 500/);
  assert.equal(b.state.pendingCancels.size, 1);
  failing = false;
  b.intervals[0]();
  await tick();
  assert.equal(b.state.pendingCancels.size, 0);
  assert.equal(b.state.claimed.size, 0);
  assert.equal(b.messages().find(m => m.role === 'amora').text, display);
});

test('TTS failure preserves the entire literal cognitive turn, not just its first speech segment', async () => {
  const local = paired(); const b = await app(local);
  b.context.queueLocalSpeech(item(0));
  await tick();
  b.context.queueLocalSpeech(item(1)); b.context.queueLocalSpeech(item(2));
  await tick();
  assert.deepEqual(b.messages().filter(m => m.role === 'amora'), [{ role: 'amora', text: display }]);
  assert.equal(new BrowserState(local).messages.length, 1);
  assert.equal(new BrowserState(local).messages[0].text, display);
  assert.equal(b.calls.filter(c => c.path.endsWith('/audio')).length, 1);
});
