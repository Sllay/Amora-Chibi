import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app-v2.js', import.meta.url), 'utf8');
function section(start, end) {
  assert.ok(source.includes(start) && source.includes(end), 'browser function boundary exists');
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
function browser() {
  const elements = new Map();
  const messages = [];
  const state = { token: 'test-browser', fillBusy: false, queueError: '', speechQueue: [], speechEpoch: 0, userSpeaking: false };
  const context = vm.createContext({ state, messages, MAX_SPEECH_QUEUE: 3, VOICE_FETCH_TIMEOUT_MS: 1000,
    AbortController, setTimeout, clearTimeout, performance,
    $: id => { if (!elements.has(id)) elements.set(id, { textContent: '', classList: { add() {}, remove() {} } }); return elements.get(id); },
    addMessage: (role, text) => messages.push({ role, text }),
    cognitiveTurnId: item => item?.metadata?.cognitive_turn_id || null,
    browserStore: { forgetPairing() {} }, pairPanel: { classList: { remove() {} } }, appPanel: { classList: { add() {} } },
  });
  vm.runInContext(section('function setVoiceStatus(', 'function addMessage('), context);
  vm.runInContext(section('async function fillSpeechQueue()', '$("stopVoice").addEventListener'), context);
  return { context, state, messages, elements };
}

test('claim HTTP 500 is visible, repeated errors are bounded, recovery clears diagnostic', async () => {
  const b = browser();
  b.context.claimNext = async () => { throw Object.assign(new Error('private response body'), { status: 500 }); };
  await b.context.fillSpeechQueue();
  await b.context.fillSpeechQueue();
  assert.equal(b.elements.get('diagQueue').textContent, 'fila/resposta indisponível: HTTP 500');
  assert.equal(b.messages.length, 1);
  assert.ok(!b.messages[0].text.includes('private'));
  assert.equal(b.state.fillBusy, false);
  b.context.claimNext = async () => null;
  await b.context.fillSpeechQueue();
  assert.equal(b.elements.get('diagQueue').textContent, '0');
});

test('claim 401 still clears browser credentials and unlocks filling', async () => {
  const b = browser();
  b.context.claimNext = async () => { throw Object.assign(new Error(), { status: 401 }); };
  await b.context.fillSpeechQueue();
  assert.equal(b.state.token, '');
  assert.equal(b.state.fillBusy, false);
});

test('text reaches the browser before remote TTS failure and remains after cancellation', async () => {
  const b = browser();
  vm.runInContext(section('function queueLocalSpeech(', 'function pumpPlayback('), b.context);
  const cancellations = [];
  b.context.apiBinary = async () => { throw Object.assign(new Error('HTTP 503'), { status: 503 }); };
  b.context.api = async path => { cancellations.push(path); return {}; };
  b.context.queueLocalSpeech({ id: 'utterance-test', text: 'Resposta textual preservada' });
  assert.equal(b.messages[0].text, 'Resposta textual preservada');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(b.messages[0].text, 'Resposta textual preservada');
  assert.equal(b.state.speechQueue.length, 0);
  assert.ok(cancellations[0].endsWith('/cancel'));
  assert.ok(b.messages.some(m => m.text.includes('Texto preservado')));
  assert.ok(!b.messages.some(m => m.text.includes('Termux')));
});

test('configured remote worker is not mislabeled as local Termux setup', async () => {
  const b = browser();
  vm.runInContext(section('async function validatePairing()', 'function getPlaybackContext('), b.context);
  b.context.api = async () => ({ voice_transport: 'amora_remote_supertonic_world', local_voice: { configured: true, available: false } });
  assert.equal(await b.context.validatePairing(), true);
  assert.match(b.elements.get('voiceLabel').textContent, /remoto/);
  assert.match(b.elements.get('diagVoice').textContent, /configurado/);
});


test('voice fetch timeout is diagnosed and releases queue without erasing text', async () => {
  const b = browser();
  vm.runInContext(section('function queueLocalSpeech(', 'function pumpPlayback('), b.context);
  b.context.VOICE_FETCH_TIMEOUT_MS = 1;
  b.context.api = async () => ({});
  b.context.apiBinary = (path, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true });
  });
  b.context.queueLocalSpeech({ id: 'timeout-test', text: 'Texto continua visível' });
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(b.messages[0].text, 'Texto continua visível');
  assert.equal(b.state.speechQueue.length, 0);
  assert.ok(b.messages.some(m => m.text.includes('tempo limite do worker')));
});
