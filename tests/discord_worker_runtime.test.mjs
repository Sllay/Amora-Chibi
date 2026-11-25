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

test('Core inbound transport budget outlives the Core cognition hard timeout', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  let capturedTimeoutMs = null;

  globalThis.fetch = async () => new Response(
    JSON.stringify({ decision: { action: 'respond' }, outbound: [] }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
  globalThis.setTimeout = (_callback, timeoutMs) => {
    capturedTimeoutMs = timeoutMs;
    return { fakeTimer: true };
  };
  globalThis.clearTimeout = () => {};

  try {
    const client = new MessagingCoreClient({
      baseUrl: 'https://core.example.test',
      token: 'm'.repeat(48),
      platform: 'discord',
    });
    await client.inbound({ message_id: 'budget-probe' });
    assert.equal(capturedTimeoutMs, 120_000);
    assert.ok(capturedTimeoutMs > 105_000);
    // Image perception occurs before Core cognition. A 120s worker cap
    // could cancel 27s vision + 105s dialogue before Core can respond.
    await client.inbound({
      message_id: 'image-budget-probe',
      incoming_image: { content_mime: 'image/png', content_base64: 'AAAA' },
    });
    assert.equal(capturedTimeoutMs, 160_000);
    await client.inbound({ message_id: 'failed-image-probe',
      incoming_image_status: 'IMAGE_TRANSFORM_FAILED' });
    assert.equal(capturedTimeoutMs, 160_000);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});


test('RUN04 same DM admission is FIFO and independent DMs run concurrently', async () => {
  const worker = makeWorker();
  const started = [], finished = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  worker.deliverNormalized = async message => {
    started.push(message.id);
    if (message.id === '1') await gate;
    finished.push(message.id);
  };
  const entry = (id, channel_id) => ({revision:1,message:{id,channel_id}});
  const one = worker.admitDiscordEntry(entry('1','dm-a'));
  const two = worker.admitDiscordEntry(entry('2','dm-a'));
  const three = worker.admitDiscordEntry(entry('3','dm-a'));
  const independent = worker.admitDiscordEntry(entry('4','dm-b'));
  await independent;
  assert.deepEqual(started,['1','4']);
  assert.deepEqual(finished,['4']);
  release();
  await Promise.all([one,two,three]);
  assert.deepEqual(finished,['4','1','2','3']);
  assert.equal(worker.dmInboundTails.size,0);
});

test('RUN04 failed admission does not discard the next independent turn', async () => {
  const worker = makeWorker();
  const seen = [];
  worker.deliverNormalized = async message => {
    seen.push(message.id);
    if (message.id === '1') throw new Error('synthetic failure');
    return message.id;
  };
  const one = worker.admitDiscordEntry({revision:1,message:{id:'1',channel_id:'dm'}});
  const two = worker.admitDiscordEntry({revision:1,message:{id:'2',channel_id:'dm'}});
  await assert.rejects(one,/synthetic failure/);
  assert.equal(await two,'2');
  assert.deepEqual(seen,['1','2']);
  assert.equal(worker.dmInboundTails.size,0);
});

test('RUN04 edit or deletion while waiting fences the obsolete revision', async () => {
  const worker = makeWorker();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const seen = [];
  const states = new Map();
  worker.runtime.state = (_, id) => states.get(id);
  worker.deliverNormalized = async message => {
    seen.push(message.id + ':' + message.content);
    if (message.id === '1') await gate;
  };
  const one = worker.admitDiscordEntry({revision:1,message:{id:'1',channel_id:'dm',content:'first'}});
  const old = worker.admitDiscordEntry({revision:1,message:{id:'2',channel_id:'dm',content:'obsolete'}});
  states.set('2',{revision:2,deleted:false});
  const edited = worker.admitDiscordEntry({revision:2,message:{id:'2',channel_id:'dm',content:'corrected'}});
  const removed = worker.admitDiscordEntry({revision:1,message:{id:'3',channel_id:'dm',content:'deleted'}});
  states.set('3',{revision:2,deleted:true});
  release();
  await Promise.all([one,old,edited,removed]);
  assert.deepEqual(seen,['1:first','2:corrected']);
  assert.equal(worker.dmInboundTails.size,0);
});
