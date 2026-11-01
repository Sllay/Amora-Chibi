import assert from 'node:assert/strict';
import test from 'node:test';

import { MessagingCoreClient } from '../workers/common/core-client.mjs';
import { DiscordWorker } from '../workers/discord/worker.mjs';

function makeWorker() {
  const core = {
    reportPresence: async () => ({}),
  };
  return new DiscordWorker({
    token: 'test-discord-token',
    core,
    rest: { request: async () => ({}) },
    runtime: {},
  });
}

test('intentional Discord disconnect drains queued outbound before closing transport', async () => {
  const worker = makeWorker();
  const order = [];
  worker.socket = { close() {} };
  worker.ready = true;
  worker.flushOutbound = async () => { order.push('flush'); };
  worker.disconnect = async (reason) => { order.push(`disconnect:${reason}`); };

  await worker.syncPresence({
    generation: 7,
    desired_connected: false,
    check_requested: false,
  });

  assert.deepEqual(order, ['flush', 'disconnect:CORE_DESIRED_DISCONNECTED']);
  assert.equal(worker.connectionGeneration, 7);
});

test('connected Discord transport flushes normally without disconnecting', async () => {
  const worker = makeWorker();
  const order = [];
  worker.socket = { close() {} };
  worker.ready = true;
  worker.flushOutbound = async () => { order.push('flush'); };
  worker.disconnect = async () => { order.push('disconnect'); };

  await worker.syncPresence({
    generation: 8,
    desired_connected: true,
    check_requested: false,
  });

  assert.deepEqual(order, ['flush']);
});

test('desired Discord connection asks connect once when no socket exists', async () => {
  const worker = makeWorker();
  const order = [];
  worker.socket = null;
  worker.ready = false;
  worker.connect = async () => { order.push('connect'); };
  worker.flushOutbound = async () => { order.push('flush'); };

  await worker.syncPresence({
    generation: 9,
    desired_connected: true,
    check_requested: false,
  });

  assert.deepEqual(order, ['connect']);
});

test('Core HTTP failures expose stable status-specific error codes to workers', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: 'temporary failure' }),
    { status: 503, headers: { 'content-type': 'application/json' } },
  );
  try {
    const client = new MessagingCoreClient({
      baseUrl: 'https://core.example.test',
      token: 'm'.repeat(48),
      platform: 'discord',
    });
    await assert.rejects(
      client.getPresence(),
      (error) => error?.code === 'CORE_HTTP_503' && error?.status === 503,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Core network failures expose a stable transport error code to workers', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError('fetch failed'); };
  try {
    const client = new MessagingCoreClient({
      baseUrl: 'https://core.example.test',
      token: 'm'.repeat(48),
      platform: 'discord',
    });
    await assert.rejects(
      client.getPresence(),
      (error) => error?.code === 'CORE_NETWORK_ERROR' && error?.status === null,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
