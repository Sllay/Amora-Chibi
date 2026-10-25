import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserState } from '../web/browser-state.mjs';
import { app, storage, token } from './helpers/web-app.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
function paired() { const local = storage(); new BrowserState(local).savePairing(token); return local; }

test('claim HTTP 500 is visible, repeated errors are bounded, recovery clears diagnostic', async () => {
  let failed = true;
  const b = await app(paired(), { fetch: path => path.startsWith('/v1/conversation/next')
    ? Response.json({ detail: 'private response body' }, { status: failed ? 500 : 200 }) : undefined });
  await b.context.fillSpeechQueue();
  assert.equal(b.nodes.get('diagQueue').textContent, 'fila/resposta indisponível: HTTP 500');
  assert.equal(b.nodes.get('chatNotice').textContent, 'fila/resposta indisponível: HTTP 500');
  assert.equal(b.messages().length, 0);
  assert.equal(b.state.fillBusy, false);
  failed = false;
  await b.context.fillSpeechQueue();
  assert.equal(b.nodes.get('diagQueue').textContent, '0');
  assert.equal(b.nodes.get('chatNotice').textContent, '');
});

test('claim 401 still clears browser credentials and unlocks filling', async () => {
  const local = paired();
  const b = await app(local, { fetch: path => path.startsWith('/v1/conversation/next') ? Response.json({}, { status: 401 }) : undefined });
  assert.equal(new BrowserState(local).pairing(), '');
  assert.equal(b.state.token, '');
  assert.equal(b.state.fillBusy, false);
});

test('text reaches the browser before remote TTS failure and remains after cancellation', async () => {
  const b = await app(paired());
  b.context.queueLocalSpeech({ id: 'utterance-test', text: 'Resposta textual preservada' });
  assert.equal(b.messages().find(m => m.role === 'amora').text, 'Resposta textual preservada');
  await tick();
  assert.equal(b.state.speechQueue.length, 0);
  assert.ok(b.calls.some(c => c.path.endsWith('/cancel')));
  assert.match(b.nodes.get('chatNotice').textContent, /Texto preservado/);
});

test('configured remote worker is not mislabeled as local Termux setup', async () => {
  const b = await app(paired());
  assert.match(b.nodes.get('voiceLabel').textContent, /remoto/);
  assert.match(b.nodes.get('diagVoice').textContent, /configurado/);
});

test('voice fetch timeout is diagnosed and releases queue without erasing text', async () => {
  const b = await app(paired());
  b.context.apiBinary = async () => { throw new DOMException('timeout', 'AbortError'); };
  b.context.queueLocalSpeech({ id: 'timeout-test', text: 'Texto continua visível' });
  await tick();
  assert.equal(b.messages().find(m => m.role === 'amora').text, 'Texto continua visível');
  assert.equal(b.state.speechQueue.length, 0);
  assert.match(b.nodes.get('chatNotice').textContent, /tempo limite do worker/);
});
