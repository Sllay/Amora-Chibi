import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DISCORD_INTENTS,
  discordReplyPayload,
  normalizeDiscordMessage,
  splitDiscordText,
} from '../workers/discord/normalize.mjs';
import {
  normalizeWhatsAppMessage,
  shouldAcceptUpsert,
} from '../workers/whatsapp/normalize.mjs';


test('Discord worker requests only guild/direct message and message-content intents', () => {
  assert.equal(DISCORD_INTENTS, 37377);
  assert.ok(DISCORD_INTENTS & (1 << 9));
  assert.ok(DISCORD_INTENTS & (1 << 12));
  assert.ok(DISCORD_INTENTS & (1 << 15));
});


test('Discord normalization preserves stable user id, native mention and reply evidence', () => {
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
  }, { botUserId: 'bot1', accountScope: 'primary' });

  assert.equal(normalized.channel_type, 'group');
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
  assert.equal(payload.message_reference.message_id, 'abc');
  assert.equal(payload.message_reference.fail_if_not_exists, false);
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
