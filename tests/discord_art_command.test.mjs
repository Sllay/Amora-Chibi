import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  discordArtCommandDefinition,
  parseDiscordArtInteraction,
  handleDiscordArtInteraction,
} from '../workers/discord/art-command.mjs';

const PNG = Buffer.concat([
  Buffer.from('89504e470d0a1a0a', 'hex'),
  Buffer.from('0000000d4948445200000400000004000802000000', 'hex'),
  Buffer.alloc(40),
]);

function interaction(prompt = 'Amora sorrindo') {
  return {
    type: 2, id: 'i1', token: 'token', application_id: 'app',
    user: { id: '12345678901234567' },
    data: {
      name: 'amora-arte',
      options: [{ name: 'prompt', value: prompt }, { name: 'preset', value: 'sticker' }],
    },
  };
}
function rest() {
  const calls = [];
  return { calls, request: async (path, request) => {
    calls.push({ path, request }); return {};
  } };
}

test('art command parses explicit prompt and preset, not unknown interactions', () => {
  assert.equal(discordArtCommandDefinition().name, 'amora-arte');
  assert.equal(parseDiscordArtInteraction(interaction())?.preset, 'sticker');
  assert.equal(parseDiscordArtInteraction(interaction(' ')), null);
  assert.equal(parseDiscordArtInteraction({ ...interaction(), type: 0 }), null);
});

test('disabled remote processing ACKs privately and makes no generation call', async () => {
  const client = rest();
  let called = false;
  const outcome = await handleDiscordArtInteraction({
    command: parseDiscordArtInteraction(interaction()),
    rest: client,
    core: { generateArt: async () => { called = true; } },
    remoteProcessingAllowed: false,
  });
  assert.equal(outcome.status, 'remote_processing_disabled');
  assert.equal(called, false);
  assert.equal(client.calls[0].request.body.data.flags, 64);
});

test('authorized generation delivers a single private multipart image', async () => {
  const client = rest();
  let payload;
  const outcome = await handleDiscordArtInteraction({
    command: parseDiscordArtInteraction(interaction()),
    rest: client,
    core: { generateArt: async (input) => {
      assert.equal(input.discord_actor_id, '12345678901234567');
      assert.equal(input.remote_processing_allowed, true);
      return { image_base64: PNG.toString('base64'), content_mime: 'image/png' };
    } },
    remoteProcessingAllowed: true,
    fetchImpl: async (url, options) => {
      assert.match(url, /^https:\/\/discord.com\/api\/v10\/webhooks\//);
      assert.equal(options.method, 'PATCH');
      assert.equal(options.body.get('files[0]').size, PNG.length);
      payload = JSON.parse(options.body.get('payload_json'));
      return { ok: true };
    },
  });
  assert.equal(outcome.status, 'sent');
  assert.equal(payload.allowed_mentions.parse.length, 0);
  assert.equal(payload.attachments[0].filename, 'amora.png');
  assert.equal(client.calls.length, 1); // initial ACK only
});

test('provider failures do not leak arbitrary upstream response messages', async () => {
  const client = rest();
  const outcome = await handleDiscordArtInteraction({
    command: parseDiscordArtInteraction(interaction()),
    rest: client,
    core: { generateArt: async () => {
      throw { payload: { detail: 'secret from provider' } };
    } },
    remoteProcessingAllowed: true,
  });
  assert.equal(outcome.status, 'failed');
  assert.equal(outcome.code, 'ART_GENERATION_UNAVAILABLE');
  assert.doesNotMatch(client.calls.at(-1).request.body.content, /secret/);
});
