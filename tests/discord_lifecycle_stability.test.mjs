import assert from 'node:assert/strict';
import test from 'node:test';

import { DiscordWorker } from '../workers/discord/worker.mjs';
import { discordReplyPayload } from '../workers/discord/normalize.mjs';
import { DiscordSocialRuntime } from '../workers/discord/social-runtime.mjs';


test('Discord outbound nonce enables provider-side create-message idempotency', () => {
  const payload = discordReplyPayload('oi', {
    replyToMessageId: 'source-1',
    nonce: 'deterministic-nonce-01',
  });
  assert.equal(payload.nonce, 'deterministic-nonce-01');
  assert.equal(payload.enforce_nonce, true);
  assert.equal(payload.message_reference.message_id, 'source-1');
});


test('Discord social runtime treats duplicate create and non-semantic update as idempotent', () => {
  const runtime = new DiscordSocialRuntime();
  const original = {
    id: 'm1',
    channel_id: 'c1',
    guild_id: 'g1',
    content: 'Amora, oi',
    author: { id: 'u1', bot: false },
    mentions: [{ id: 'bot1' }],
    attachments: [],
  };
  const first = runtime.recordCreate(original);
  const generation = runtime.snapshot('c1').generation;
  const duplicate = runtime.recordCreate(structuredClone(original));

  assert.equal(first.revision, 1);
  assert.equal(duplicate.duplicate, true);
  assert.equal(duplicate.revision, 1);
  assert.equal(runtime.snapshot('c1').generation, generation);

  const embedOnly = runtime.recordUpdate({ id: 'm1', channel_id: 'c1', embeds: [{ title: 'preview' }] });
  assert.equal(embedOnly.semanticChanged, false);
  assert.equal(embedOnly.duplicate, true);
  assert.equal(embedOnly.revision, 1);
  assert.equal(runtime.snapshot('c1').generation, generation);

  const semantic = runtime.recordUpdate({ id: 'm1', channel_id: 'c1', content: 'Amora, pera' });
  assert.equal(semantic.semanticChanged, true);
  assert.equal(semantic.revision, 2);
  assert.equal(runtime.snapshot('c1').generation, generation + 1);
});


test('Discord worker never invalidates Core for edits authored by the bot itself', async () => {
  const calls = [];
  const runtime = new DiscordSocialRuntime();
  runtime.recordCreate({
    id: 'bot-message',
    channel_id: 'c1',
    content: 'resposta',
    author: { id: 'bot1', bot: true },
    mentions: [],
    attachments: [],
  });
  const worker = new DiscordWorker({
    token: 'test-token',
    core: {
      reportPresence: async () => ({}),
      invalidateSource: async (payload) => { calls.push(payload); },
    },
    rest: { request: async () => ({}) },
    runtime,
  });
  worker.botUserId = 'bot1';

  await worker.handleMessageUpdate({
    id: 'bot-message',
    channel_id: 'c1',
    content: 'resposta editada pelo próprio bot',
    author: { id: 'bot1', bot: true },
  });

  assert.deepEqual(calls, []);
});


test('Discord worker reconciles a surviving READY socket after Core restart', async () => {
  const reports = [];
  const core = {
    getPresence: async () => ({
      generation: 7,
      state: 'disconnected',
      desired_connected: true,
      check_requested: false,
    }),
    reportPresence: async (state, options) => {
      reports.push([state, options]);
      return {
        generation: 7,
        state,
        desired_connected: true,
        check_requested: false,
      };
    },
  };
  const worker = new DiscordWorker({
    token: 'test-token',
    core,
    rest: { request: async () => ({}) },
    runtime: new DiscordSocialRuntime(),
  });
  worker.socket = { close() {} };
  worker.ready = true;
  worker.flushOutbound = async () => {};

  await worker.syncPresence(await core.getPresence());

  assert.deepEqual(reports.map(([state]) => state), ['connecting', 'connected']);
  assert.equal(worker.connectionGeneration, 7);
});


test('Discord send uses stable unique nonces per chunk so a retry cannot duplicate chunks', async () => {
  const bodies = [];
  const worker = new DiscordWorker({
    token: 'test-token',
    core: { reportPresence: async () => ({}) },
    rest: {
      request: async (_path, options) => {
        bodies.push(options.body);
        return { id: `created-${bodies.length}`, channel_id: 'c1', author: { id: 'bot1', bot: true } };
      },
    },
    runtime: new DiscordSocialRuntime(),
  });
  const item = {
    id: '01234567-89ab-cdef-0123-456789abcdef',
    conversation_id: 'c1',
    reply_to_message_id: 'source-1',
    text: 'x'.repeat(2500),
  };

  await worker.sendOutbound(item);
  const firstRun = bodies.map((body) => body.nonce);
  bodies.length = 0;
  await worker.sendOutbound(item);
  const secondRun = bodies.map((body) => body.nonce);

  assert.equal(firstRun.length, 2);
  assert.notEqual(firstRun[0], firstRun[1]);
  assert.deepEqual(secondRun, firstRun);
  assert.ok(bodies.every((body) => body.enforce_nonce === true));
});
