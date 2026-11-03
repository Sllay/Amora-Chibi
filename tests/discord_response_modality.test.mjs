import assert from 'node:assert/strict';
import test from 'node:test';

import { DiscordExpressionWorker } from '../workers/discord/expression-worker.mjs';
import { discordReplyPayload, normalizeDiscordMessage } from '../workers/discord/normalize.mjs';


test('Discord accepts sticker-only inbound as native expression input', () => {
  const normalized = normalizeDiscordMessage({
    id: 'sticker-msg-1',
    channel_id: 'channel-1',
    guild_id: 'guild-1',
    timestamp: '2026-11-02T04:00:00Z',
    content: '',
    author: { id: 'user-1', username: 'bk', bot: false },
    mentions: [],
    attachments: [],
    sticker_items: [
      { id: 'sticker-1', name: 'morre', format_type: 1 },
    ],
  }, { botUserId: 'bot-1' });

  assert.ok(normalized);
  assert.equal(normalized.text, '');
  assert.equal(normalized.expressions.length, 1);
  assert.equal(normalized.expressions[0].native_id, 'sticker-1');
  assert.equal(normalized.attachments[0].media_type, 'application/vnd.discord.sticker');
});


test('Discord keeps trusted guild expression catalog separate from message content', () => {
  const normalized = normalizeDiscordMessage({
    id: 'msg-2',
    channel_id: 'channel-1',
    guild_id: 'guild-1',
    timestamp: '2026-11-02T04:00:00Z',
    content: 'Amora, abre esse sorrisão pro pai',
    author: { id: 'user-1', username: 'bk', bot: false },
    mentions: [],
    attachments: [],
    sticker_items: [],
    __amora_sticker_catalog: [
      { id: 'happy-1', name: 'happy', description: 'feliz', tags: 'smile,happy', format_type: 1 },
      { id: 'angry-1', name: 'rage', description: 'muito bravo', tags: 'raiva,angry', format_type: 1 },
    ],
  }, { botUserId: 'bot-1' });

  assert.equal(normalized.expressions.length, 0);
  assert.equal(normalized.available_expressions.length, 2);
  assert.equal(normalized.available_expressions[0].native_id, 'happy-1');
  assert.deepEqual(normalized.available_expressions[0].tags, ['smile', 'happy']);
  assert.equal(normalized.text, 'Amora, abre esse sorrisão pro pai');
});


test('Discord expression worker caches guild sticker catalog inside TTL', async () => {
  let fetches = 0;
  const worker = new DiscordExpressionWorker({
    token: 'test-discord-token',
    core: {},
    rest: {
      request: async (path) => {
        assert.equal(path, '/guilds/guild-1/stickers');
        fetches += 1;
        return [{ id: 'sticker-1', name: 'morre', tags: 'laugh,lol', format_type: 1 }];
      },
    },
    runtime: {},
  });

  const first = await worker.guildStickerCatalog('guild-1');
  const second = await worker.guildStickerCatalog('guild-1');
  assert.equal(fetches, 1);
  assert.equal(first[0].id, 'sticker-1');
  assert.deepEqual(second, first);
});


test('Discord sticker-only payload needs no fake text and keeps reply safety', () => {
  const payload = discordReplyPayload('', {
    replyToMessageId: 'source-1',
    nonce: 'nonce-1',
    stickerIds: ['sticker-1'],
  });

  assert.equal(payload.content, undefined);
  assert.deepEqual(payload.sticker_ids, ['sticker-1']);
  assert.deepEqual(payload.allowed_mentions.parse, []);
  assert.equal(payload.allowed_mentions.replied_user, false);
  assert.equal(payload.message_reference.message_id, 'source-1');
  assert.equal(payload.enforce_nonce, true);
});


test('expression worker sends Core-selected persisted sticker id', async () => {
  const requests = [];
  const remembered = [];
  const worker = new DiscordExpressionWorker({
    token: 'test-discord-token',
    core: {},
    rest: {
      request: async (path, init) => {
        requests.push({ path, init });
        return { id: 'discord-out-1', channel_id: 'channel-1' };
      },
    },
    runtime: {
      rememberBotMessage: (message) => remembered.push(message),
    },
  });

  const externalId = await worker.sendOutbound({
    id: 'outbound-1',
    conversation_id: 'channel-1',
    text: 'hehe, claro papis',
    reply_to_message_id: 'source-1',
    metadata: {
      response_modality: 'text+expression',
      transport_expressions: [
        {
          kind: 'sticker',
          native_id: 'smile-1',
          name: 'wide-smile',
          guild_or_group_id: 'guild-1',
        },
      ],
    },
  });

  assert.equal(externalId, 'discord-out-1');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/channels/channel-1/messages');
  assert.equal(requests[0].init.method, 'POST');
  assert.equal(requests[0].init.body.content, 'hehe, claro papis');
  assert.deepEqual(requests[0].init.body.sticker_ids, ['smile-1']);
  assert.equal(requests[0].init.body.message_reference.message_id, 'source-1');
  assert.equal(remembered.length, 1);
});


test('expression worker can deliver expression-only modality', async () => {
  const requests = [];
  const worker = new DiscordExpressionWorker({
    token: 'test-discord-token',
    core: {},
    rest: {
      request: async (path, init) => {
        requests.push({ path, init });
        return { id: 'discord-out-2', channel_id: 'channel-1' };
      },
    },
    runtime: { rememberBotMessage: () => {} },
  });

  await worker.sendOutbound({
    id: 'outbound-2',
    conversation_id: 'channel-1',
    text: '',
    reply_to_message_id: 'source-2',
    metadata: {
      response_modality: 'expression',
      transport_expressions: [
        { kind: 'sticker', native_id: 'smile-1', guild_or_group_id: 'guild-1' },
      ],
    },
  });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.body.content, undefined);
  assert.deepEqual(requests[0].init.body.sticker_ids, ['smile-1']);
});
