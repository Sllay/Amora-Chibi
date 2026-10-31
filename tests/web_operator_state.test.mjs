import assert from 'node:assert/strict';
import test from 'node:test';

import { OPERATOR_KEY, OperatorState, isOperatorCredential } from '../web/operator-state.mjs';

function token(expirySeconds) {
  const raw = `operator:${expirySeconds}:0123456789abcdef0123456789abcdef`;
  const payload = Buffer.from(raw, 'utf8').toString('base64url');
  return `browser.v3.${payload}.${'a'.repeat(64)}`;
}

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test('operator credential shape and expiry are bounded locally', () => {
  const now = Date.UTC(2026, 9, 31, 22, 0, 0);
  const valid = token(Math.floor(now / 1000) + 60);
  const expired = token(Math.floor(now / 1000) - 1);
  assert.equal(isOperatorCredential(valid, now), true);
  assert.equal(isOperatorCredential(expired, now), false);
  assert.equal(isOperatorCredential('browser.v2.invalid', now), false);
});

test('operator browser state clears expired tokens and never accepts web role', () => {
  const storage = new MemoryStorage();
  const state = new OperatorState(storage);
  const live = token(Math.floor(Date.now() / 1000) + 600);
  state.save(live);
  assert.equal(state.get(), live);
  assert.equal(storage.getItem(OPERATOR_KEY), live);

  storage.setItem(OPERATOR_KEY, token(Math.floor(Date.now() / 1000) - 10));
  assert.equal(state.get(), '');
  assert.equal(storage.getItem(OPERATOR_KEY), null);
  assert.throws(() => state.save(`browser.v2.${'a'.repeat(20)}.${'b'.repeat(64)}`));
});
