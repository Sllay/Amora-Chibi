import assert from 'node:assert/strict';
import test from 'node:test';

import { StickerMediaError } from '../workers/discord/sticker-media.mjs';
import { DiscordVisualEnrichmentWorker } from '../workers/discord/visual-worker.mjs';

function work(overrides = {}) {
  return {
    platform: 'discord',
    account_scope: 'primary',
    scope_id: 'guild-1',
    kind: 'sticker',
    native_id: '123456789012345678',
    expected_fingerprint: 'a'.repeat(64),
    claim_token: 'claim-token-1234567890',
    metadata: { format_type: 1 },
    ...overrides,
  };
}

function coreStub({ claimed = [work()], submitImpl = null, deferImpl = null } = {}) {
  const calls = {
    claim: [],
    submit: [],
    defer: [],
  };
  let claimIndex = 0;
  return {
    calls,
    async claimVisual(limit, leaseSeconds) {
      calls.claim.push({ limit, leaseSeconds });
      const current = claimed[claimIndex++] || null;
      return { work: current ? [current] : [] };
    },
    async submitVisualContent(item, payload) {
      calls.submit.push({ item, payload });
      if (submitImpl) return submitImpl(item, payload);
      return { status: 'enriched' };
    },
    async deferVisual(item, payload) {
      calls.defer.push({ item, payload });
      if (deferImpl) return deferImpl(item, payload);
      return { status: 'deferred' };
    },
  };
}

const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);

function mediaStub(bytes = png) {
  return async ({ nativeId, formatType }) => ({
    bytes,
    mime: 'image/png',
    stickerId: nativeId,
    formatType,
    sourceHost: 'cdn.discordapp.com',
  });
}

test('drain returns zero when there is no visual work', async () => {
  const core = coreStub({ claimed: [] });
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub() });

  assert.equal(await worker.drain(), 0);
  assert.equal(core.calls.submit.length, 0);
  assert.equal(core.calls.defer.length, 0);
});

test('successful sticker work fetches bytes and submits base64 to Core', async () => {
  const core = coreStub();
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 1 });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.submit.length, 1);
  assert.equal(core.calls.defer.length, 0);
  assert.equal(core.calls.submit[0].payload.contentMime, 'image/png');
  assert.equal(core.calls.submit[0].payload.contentBase64, Buffer.from(png).toString('base64'));
});

test('deterministic media validation errors are deferred with stable worker code', async () => {
  const core = coreStub();
  const worker = new DiscordVisualEnrichmentWorker({
    core,
    fetchMedia: async () => {
      throw new StickerMediaError('INVALID_STICKER_CONTENT', 'bad sticker bytes');
    },
    maxDrain: 1,
  });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.submit.length, 0);
  assert.equal(core.calls.defer.length, 1);
  assert.equal(core.calls.defer[0].payload.errorCode, 'DISCORD_INVALID_STICKER_CONTENT');
  assert.equal(core.calls.defer[0].payload.retryAfterSeconds, 21_600);
});

test('unsupported work kind is deferred without attempting media fetch', async () => {
  const core = coreStub({ claimed: [work({ kind: 'emoji' })] });
  let fetched = false;
  const worker = new DiscordVisualEnrichmentWorker({
    core,
    fetchMedia: async () => {
      fetched = true;
      return mediaStub()({ nativeId: '1', formatType: 1 });
    },
    maxDrain: 1,
  });

  assert.equal(await worker.drain(), 1);
  assert.equal(fetched, false);
  assert.equal(core.calls.defer[0].payload.errorCode, 'DISCORD_UNSUPPORTED_VISUAL_KIND');
});

test('stale Core 409 after submit does not create another defer mutation', async () => {
  const error = Object.assign(new Error('stale'), { status: 409, code: 'CORE_HTTP_409' });
  const core = coreStub({ submitImpl: async () => { throw error; } });
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 1 });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.submit.length, 1);
  assert.equal(core.calls.defer.length, 0);
});

test('Core 422 releases deterministic rejected content through defer', async () => {
  const error = Object.assign(new Error('invalid'), { status: 422, code: 'CORE_HTTP_422' });
  const core = coreStub({ submitImpl: async () => { throw error; } });
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 1 });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.defer.length, 1);
  assert.equal(core.calls.defer[0].payload.errorCode, 'CORE_VISUAL_CONTENT_REJECTED');
});

test('Core 503 is not double-deferred because Core already classified provider failure', async () => {
  const error = Object.assign(new Error('provider timeout'), { status: 503, code: 'CORE_HTTP_503' });
  const core = coreStub({ submitImpl: async () => { throw error; } });
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 1 });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.defer.length, 0);
});

test('ambiguous submit network failure leaves lease untouched for safe recovery', async () => {
  const error = Object.assign(new Error('socket reset'), { code: 'CORE_NETWORK_ERROR' });
  const core = coreStub({ submitImpl: async () => { throw error; } });
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 2 });

  assert.equal(await worker.drain(), 1);
  assert.equal(core.calls.defer.length, 0);
  assert.equal(core.calls.claim.length, 1);
});

test('processing lock prevents overlapping visual drains', async () => {
  let releaseClaim;
  const claimGate = new Promise((resolve) => { releaseClaim = resolve; });
  const item = work();
  let firstClaim = true;
  const core = {
    calls: { claim: 0, submit: 0 },
    async claimVisual() {
      this.calls.claim += 1;
      if (!firstClaim) return { work: [] };
      firstClaim = false;
      await claimGate;
      return { work: [item] };
    },
    async submitVisualContent() {
      this.calls.submit += 1;
      return { status: 'enriched' };
    },
    async deferVisual() {
      throw new Error('unexpected defer');
    },
  };
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub(), maxDrain: 1 });

  const first = worker.drain();
  await Promise.resolve();
  assert.equal(await worker.drain(), 0);
  releaseClaim();
  assert.equal(await first, 1);
  assert.equal(core.calls.submit, 1);
});

test('stop prevents future drains without mutating Core', async () => {
  const core = coreStub();
  const worker = new DiscordVisualEnrichmentWorker({ core, fetchMedia: mediaStub() });
  worker.stop();

  assert.equal(await worker.drain(), 0);
  assert.equal(core.calls.claim.length, 0);
});
