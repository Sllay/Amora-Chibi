import assert from 'node:assert/strict';
import test from 'node:test';

import { DiscordRestClient } from '../workers/discord/http.mjs';
import {
  DISCORD_INTENTS,
  discordEditMessageId,
  discordReplyPayload,
  normalizeDiscordMessage,
  normalizeDiscordReaction,
  splitDiscordText,
} from '../workers/discord/normalize.mjs';
import { DiscordSocialRuntime } from '../workers/discord/social-runtime.mjs';
import {
  normalizeWhatsAppMessage,
  shouldAcceptUpsert,
} from '../workers/whatsapp/normalize.mjs';


test('Discord worker requests messages, reactions and message-content intents without privileged presence/member intents', () => {
  assert.equal(DISCORD_INTENTS, 46593);
  assert.ok(DISCORD_INTENTS & (1 << 9));
  assert.ok(DISCORD_INTENTS & (1 << 10));
  assert.ok(DISCORD_INTENTS & (1 << 12));
  assert.ok(DISCORD_INTENTS & (1 << 13));
  assert.ok(DISCORD_INTENTS & (1 << 15));
  assert.equal(DISCORD_INTENTS & (1 << 1), 0);
  assert.equal(DISCORD_INTENTS & (1 << 8), 0);
});


test('Discord normalization preserves stable user id, native mention, reply and transport revision evidence', () => {
  const normalized = normalizeDiscordMessage({
    id: 'm1',
    channel_id: 'c1',
    guild_id: 'g1',
    timestamp: '2026-10-31T20:00:00Z',
    content: 'e aí Amora',
    author: { id: 'u1', username: 'bk', global_name: 'BK', bot: false },
    member: { nick: 'Pai' },
    mentions: [{ id: 'bot1' }, { id: 'u2' }],
    attachments: [],
    message_reference: { message_id: 'old1' },
    referenced_message: { author: { id: 'bot1' } },
  }, {
    botUserId: 'bot1',
    accountScope: 'primary',
    transportRevision: 3,
    syntheticMessageId: discordEditMessageId('m1', 3),
  });

  assert.equal(normalized.channel_type, 'group');
  assert.equal(normalized.message_id, 'm1#edit:3');
  assert.equal(normalized.native_message_id, 'm1');
  assert.equal(normalized.transport_revision, 3);
  assert.equal(normalized.sender_identifiers[0].identifier_type, 'discord_user_id');
  assert.equal(normalized.sender_identifiers[0].external_actor_id, 'u1');
  assert.equal(normalized.sender_display_name, 'Pai');
  assert.equal(normalized.mentions_amora, true);
  assert.deepEqual(normalized.mentioned_actor_ids, ['u2']);
  assert.equal(normalized.reply_to_message_id, 'old1');
  assert.equal(normalized.reply_to_amora, true);
});


test('Discord ignores bot/self messages and outbound reply suppresses accidental mentions', () => {
  assert.equal(normalizeDiscordMessage({
    id: 'm2', channel_id: 'c1', author: { id: 'bot1', bot: true }, content: 'x', attachments: [], mentions: [],
  }, { botUserId: 'bot1' }), null);

  const chunks = splitDiscordText('x'.repeat(4500));
  assert.equal(chunks.length, 3);
  assert.ok(chunks.every((item) => item.length <= 2000));
  const payload = discordReplyPayload('@everyone oi', { replyToMessageId: 'abc' });
  assert.deepEqual(payload.allowed_mentions.parse, []);
  assert.equal(payload.allowed_mentions.replied_user, false);
  assert.equal(payload.message_reference.message_id, 'abc');
  assert.equal(payload.message_reference.fail_if_not_exists, false);
});


test('Discord social runtime invalidates outbound after edit or delete while unknown cache state degrades open', () => {
  let clock = 1000;
  const runtime = new DiscordSocialRuntime({ clock: () => clock });
  const created = runtime.recordCreate({
    id: 'm1', channel_id: 'c1', guild_id: 'g1', content: 'Amora, oi',
    author: { id: 'u1', bot: false }, mentions: [], attachments: [],
  });
  assert.equal(created.revision, 1);
  assert.deepEqual(runtime.validateOutbound({
    conversation_id: 'c1', source_message_id: 'm1', metadata: { transport_revision: 1 },
  }).ok, true);

  clock += 1;
  const edited = runtime.recordUpdate({ id: 'm1', channel_id: 'c1', content: 'Amora, pera' });
  assert.equal(edited.revision, 2);
  assert.equal(edited.contentChanged, true);
  const stale = runtime.validateOutbound({
    conversation_id: 'c1', source_message_id: 'm1', metadata: { transport_revision: 1 },
  });
  assert.equal(stale.ok, false);
  assert.equal(stale.reason, 'SOURCE_REVISION_CHANGED');

  runtime.recordDelete({ id: 'm1', channel_id: 'c1', guild_id: 'g1' });
  assert.equal(runtime.validateOutbound({
    conversation_id: 'c1', source_message_id: 'm1', metadata: { transport_revision: 2 },
  }).reason, 'SOURCE_DELETED');

  assert.equal(runtime.validateOutbound({
    conversation_id: 'c2', source_message_id: 'unknown', metadata: { transport_revision: 1 },
  }).reason, 'SOURCE_STATE_UNKNOWN');
});


test('Discord social runtime keeps reactions as bounded transport evidence instead of fake utterances', () => {
  const runtime = new DiscordSocialRuntime();
  const signal = runtime.recordReaction({
    message_id: 'm1', channel_id: 'c1', guild_id: 'g1', user_id: 'u1', emoji: { name: '❤️' },
  }, true);
  assert.equal(signal.added, true);
  assert.equal(runtime.snapshot('c1').reactionCount, 1);
  runtime.recordReaction({
    message_id: 'm1', channel_id: 'c1', guild_id: 'g1', user_id: 'u1', emoji: { name: '❤️' },
  }, false);
  assert.equal(runtime.snapshot('c1').reactionCount, 0);
  assert.deepEqual(normalizeDiscordReaction({
    message_id: 'm1', channel_id: 'c1', user_id: 'u1', emoji: { name: '👍' },
  }), {
    platform: 'discord', conversation_id: 'c1', message_id: 'm1', guild_or_group_id: null,
    actor_id: 'u1', emoji: 'name:👍', added: true,
  });
});


test('Discord REST client obeys 429 retry_after and succeeds without a hot retry loop', async () => {
  const waits = [];
  let calls = 0;
  const client = new DiscordRestClient({
    token: 'test-token',
    random: () => 0,
    sleep: async (ms) => { waits.push(ms); },
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) return new Response(JSON.stringify({ retry_after: 0.5 }), { status: 429, headers: { 'content-type': 'application/json' } });
      return new Response(JSON.stringify({ id: 'ok' }), { status: 200, headers: { 'content-type': 'application/json' } });
    },
  });
  assert.deepEqual(await client.request('/test'), { id: 'ok' });
  assert.equal(calls, 2);
  assert.deepEqual(waits, [500]);
});


test('Discord REST client treats missing permission as terminal and does not retry', async () => {
  let calls = 0;
  const client = new DiscordRestClient({
    token: 'test-token',
    sleep: async () => { throw new Error('must not sleep'); },
    fetchImpl: async () => {
      calls += 1;
      return new Response('{}', { status: 403 });
    },
  });
  await assert.rejects(
    () => client.request('/channels/c1/messages'),
    (error) => error.status === 403 && error.code === 'MISSING_PERMISSION' && error.retryable === false,
  );
  assert.equal(calls, 1);
});


test('WhatsApp accepts only real-time notify upserts and rejects requestId defense-in-depth', () => {
  assert.equal(shouldAcceptUpsert({ type: 'notify', messages: [] }), true);
  assert.equal(shouldAcceptUpsert({ type: 'append', messages: [] }), false);
  assert.equal(shouldAcceptUpsert({ type: 'notify', requestId: 'spoof', messages: [] }), false);
});


test('WhatsApp group normalization exposes PN and E164 evidence without confusing group with person', () => {
  const normalized = normalizeWhatsAppMessage({
    key: {
      id: 'wa1',
      remoteJid: '120363000000@g.us',
      participant: '558199999999:7@s.whatsapp.net',
      fromMe: false,
    },
    pushName: 'BK',
    messageTimestamp: 1793476800,
    message: {
      extendedTextMessage: {
        text: 'Amora, responde',
        contextInfo: {
          stanzaId: 'amora-msg-1',
          participant: '551188888888:4@s.whatsapp.net',
          mentionedJid: ['551188888888@s.whatsapp.net', '557777777777@s.whatsapp.net'],
        },
      },
    },
  }, {
    ownJids: ['551188888888:12@s.whatsapp.net'],
    accountScope: 'primary',
  });

  assert.equal(normalized.channel_type, 'group');
  assert.equal(normalized.conversation_id, '120363000000@g.us');
  assert.equal(normalized.sender_identifiers[0].external_actor_id, '558199999999@s.whatsapp.net');
  assert.ok(normalized.sender_identifiers.some((item) => item.identifier_type === 'phone_e164' && item.external_actor_id === '+558199999999'));
  assert.equal(normalized.mentions_amora, true);
  assert.deepEqual(normalized.mentioned_actor_ids, ['557777777777@s.whatsapp.net']);
  assert.equal(normalized.reply_to_message_id, 'amora-msg-1');
  assert.equal(normalized.reply_to_amora, true);
});


test('WhatsApp unwraps ephemeral captions and reports attachment metadata without analyzing media', () => {
  const normalized = normalizeWhatsAppMessage({
    key: { id: 'wa2', remoteJid: '558188888888@s.whatsapp.net', fromMe: false },
    messageTimestamp: 1793476800,
    message: {
      ephemeralMessage: {
        message: {
          imageMessage: {
            caption: 'olha isso',
            mimetype: 'image/jpeg',
            fileLength: 1234,
          },
        },
      },
    },
  });
  assert.equal(normalized.channel_type, 'dm');
  assert.equal(normalized.text, 'olha isso');
  assert.equal(normalized.attachments.length, 1);
  assert.equal(normalized.attachments[0].media_type, 'image/jpeg');
  assert.equal(normalized.attachments[0].size_bytes, 1234);
});
