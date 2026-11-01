import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveHealthPort } from '../workers/discord/entrypoint.mjs';

test('Discord worker health port is optional', () => {
  assert.equal(resolveHealthPort({}), null);
});

test('Discord worker health port accepts explicit or platform PORT', () => {
  assert.equal(resolveHealthPort({ PORT: '8080' }), 8080);
  assert.equal(resolveHealthPort({ AMORA_WORKER_HEALTH_PORT: '9000', PORT: '8080' }), 9000);
});

test('Discord worker health port rejects malformed values', () => {
  assert.throws(() => resolveHealthPort({ PORT: 'http' }), /numeric/);
  assert.throws(() => resolveHealthPort({ PORT: '70000' }), /out of range/);
});
