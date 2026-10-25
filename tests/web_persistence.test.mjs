import test from 'node:test';
import assert from 'node:assert/strict';
import { app } from './helpers/web-app.mjs';
import { BrowserState, HISTORY_KEY, PAIRING_KEY, isWebCredential } from '../web/browser-state.mjs';

function storage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key), data };
}
const token = `browser.v2.${Buffer.from(`web:${'a'.repeat(32)}`).toString('base64url')}.${'b'.repeat(64)}`;

test('pairing survives reopen; forgetting pairing preserves history', () => {
  const local = storage();
  const first = new BrowserState(local);
  first.savePairing(token);
  first.appendMessage('user', 'oi');
  const reopened = new BrowserState(local, storage());
  assert.equal(reopened.pairing(), token);
  reopened.forgetPairing();
  const again = new BrowserState(local);
  assert.equal(again.pairing(), '');
  assert.deepEqual(again.messages, [{ role: 'user', text: 'oi' }]);
});

test('admin, provider, Avatar and legacy tokens are never persisted or migrated', () => {
  const local = storage(); const session = storage();
  local.setItem('amora.web.token.v1', 'old-admin-secret');
  session.setItem('amora.web.token.v1', 'old-admin-secret');
  const state = new BrowserState(local, session);
  for (const value of ['old-admin-secret', 'gsk_example', 'browser.v1.anything.signature', `browser.v2.${Buffer.from(`avatar:${'a'.repeat(32)}`).toString('base64url')}.${'b'.repeat(64)}`]) {
    assert.equal(isWebCredential(value), false);
    assert.throws(() => state.savePairing(value));
  }
  assert.equal(local.getItem('amora.web.token.v1'), null);
  assert.equal(session.getItem('amora.web.token.v1'), null);
  assert.equal(local.getItem(PAIRING_KEY), null);
});

test('history restores in insertion order and repeated response IDs are not appended', () => {
  const local = storage(); const state = new BrowserState(local);
  assert.equal(state.appendMessage('user', 'oi'), true);
  assert.equal(state.appendMessage('amora', 'resposta', 'speech-1'), true);
  assert.equal(state.appendMessage('amora', 'resposta', 'speech-1'), false);
  assert.equal(state.appendMessage('user', 'oi'), true); // Intentional repeated input.
  const reopened = new BrowserState(local);
  assert.deepEqual(reopened.messages.map(m => m.text), ['oi', 'resposta', 'oi']);
  assert.equal(reopened.appendMessage('amora', 'resposta', 'speech-1'), false);
});

test('51st message prunes only the oldest of its role, retaining chronological order', () => {
  const state = new BrowserState(storage());
  for (let n = 0; n < 51; n++) {
    state.appendMessage('user', `u${n}`);
    state.appendMessage('amora', `a${n}`, `id-${n}`);
    assert.ok(state.messages.filter(m => m.role === 'user').length <= 50);
    assert.ok(state.messages.filter(m => m.role === 'amora').length <= 50);
    assert.ok(state.messages.length <= 100);
  }
  assert.equal(state.messages.length, 100);
  assert.deepEqual(state.messages.slice(0, 4).map(m => m.text), ['u1', 'a1', 'u2', 'a2']);
  assert.deepEqual(state.messages.slice(-2).map(m => m.text), ['u50', 'a50']);
  for (let n = 51; n < 151; n++) state.appendMessage('user', `u${n}`);
  assert.equal(state.messages.length, 100);
  assert.deepEqual(state.messages.filter(m => m.role === 'amora').map(m => m.text), Array.from({ length: 50 }, (_, i) => `a${i + 1}`));
});

test('system, errors, technical metadata and credential material never enter history', () => {
  const local = storage(); const state = new BrowserState(local);
  for (const role of ['system', 'error', 'diagnostic', 'provider']) assert.equal(state.appendMessage(role, 'HTTP 500 token-value'), false);
  for (const text of [token, `Bearer ${token}`, 'GROQ_API_KEY=gsk_private_example_long_key']) assert.equal(state.appendMessage('user', text), false);
  assert.equal(state.appendMessage('amora', 'private', 'private-id', 'SECRET'), false);
  assert.equal(state.appendMessage('user', 'auth material', undefined, 'AUTH'), false);
  assert.equal(local.getItem(HISTORY_KEY), null);
  state.appendMessage('user', 'conversa normal');
  assert.deepEqual(JSON.parse(local.getItem(HISTORY_KEY)), { version: 1, messages: [{ role: 'user', text: 'conversa normal' }] });
});

test('clearing conversation preserves pairing and independently removes history', () => {
  const local = storage(); const state = new BrowserState(local);
  state.savePairing(token); state.appendMessage('amora', 'oi', 'id-1');
  state.clearHistory();
  assert.equal(state.pairing(), token);
  assert.equal(local.getItem(HISTORY_KEY), null);
  assert.deepEqual(new BrowserState(local).messages, []);
});

for (const raw of ['{invalid', '{}', 'null', '[]', JSON.stringify({ version: 2, messages: [] }), JSON.stringify({ version: 1, messages: [{ role: 'system', text: 'error' }] }), JSON.stringify({ version: 1, messages: [{ role: 'user', text: 'oi', token: 'secret' }] }), JSON.stringify({ version: 1, messages: [{ role: 'amora', text: 42 }] })]) {
  test(`malformed history resets only history (${raw.slice(0, 32)})`, () => {
    const local = storage(); local.setItem(PAIRING_KEY, token); local.setItem(HISTORY_KEY, raw);
    const state = new BrowserState(local);
    assert.deepEqual(state.messages, []); assert.equal(state.pairing(), token);
    assert.equal(local.getItem(HISTORY_KEY), null);
  });
}

test('unavailable browser storage does not crash history; pairing save reports failure', () => {
  const unavailable = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }, removeItem() { throw new Error('denied'); } };
  const state = new BrowserState(unavailable, unavailable);
  assert.equal(state.pairing(), '');
  assert.doesNotThrow(() => state.appendMessage('user', 'oi'));
  assert.throws(() => state.savePairing(token), /armazenamento local/);
  assert.doesNotThrow(() => state.clearHistory());
});

test('actual app pairs once, reopens with history, and forget/clear actions are independent', async () => {
  const local = storage(); const first = await app(local);
  first.nodes.get('pairCode').value = '123456';
  await first.nodes.get('pairForm').listeners.submit({ preventDefault() {} });
  assert.equal(local.getItem(PAIRING_KEY), token);
  first.context.addMessage('user', 'primeira');
  first.context.addMessage('amora', 'segunda', 'reply-1');
  first.context.addMessage('system', 'HTTP 500 diagnóstico');
  const reopened = await app(local);
  assert.equal(reopened.nodes.get('pairPanel').classList.contains('hidden'), true);
  assert.equal(reopened.calls.filter(c => c.path === '/v1/pairing/redeem').length, 0);
  assert.deepEqual(reopened.messages().filter(m => ['user', 'amora'].includes(m.role)).map(m => m.text), ['primeira', 'segunda']);
  assert.equal(reopened.calls.filter(c => c.request.method === 'POST' || c.path.endsWith('/audio')).length, 0);
  reopened.context.restoreConversation();
  reopened.context.addMessage('amora', 'segunda', 'reply-1');
  assert.equal(reopened.messages().filter(m => m.role === 'amora').length, 1);
  await reopened.nodes.get('forgetPairing').listeners.click();
  assert.equal(local.getItem(PAIRING_KEY), null);
  assert.equal(JSON.parse(local.getItem(HISTORY_KEY)).messages.length, 2);
  assert.equal(reopened.nodes.get('pairPanel').classList.contains('hidden'), false);
  local.setItem(PAIRING_KEY, token);
  const again = await app(local);
  again.nodes.get('clearConversation').listeners.click();
  assert.equal(again.nodes.get('clearDialog').open, true);
  again.nodes.get('confirmClear').listeners.click();
  assert.equal(local.getItem(PAIRING_KEY), token);
  assert.equal(local.getItem(HISTORY_KEY), null);
  assert.equal(again.nodes.get('messages').children.length, 0);
  assert.equal(again.calls.filter(c => !['/health', '/v1/web/state'].includes(c.path) && !c.path.startsWith('/v1/conversation/next')).length, 0);
});

test('actual app invalid credential removes pairing, temporary server error does not', async () => {
  for (const status of [401, 500]) {
    const local = storage(); new BrowserState(local).savePairing(token);
    new BrowserState(local).appendMessage('user', 'histórico mantido');
    const current = await app(local, { authStatus: status });
    assert.equal(local.getItem(PAIRING_KEY), status === 401 ? null : token);
    assert.equal(JSON.parse(local.getItem(HISTORY_KEY)).messages.length, 1);
    assert.equal(current.nodes.get('pairPanel').classList.contains('hidden'), status !== 401);
  }
});

test('actual app keeps response text persisted after TTS fails', async () => {
  const local = storage(); new BrowserState(local).savePairing(token);
  const current = await app(local);
  current.context.queueLocalSpeech({ id: 'failed-tts', text: 'Resposta sem áudio', privacy_scope: 'PERSONAL' });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(current.messages().some(m => m.role === 'amora' && m.text === 'Resposta sem áudio'));
  assert.deepEqual(new BrowserState(local).messages, [{ role: 'amora', text: 'Resposta sem áudio', id: 'failed-tts' }]);
});
