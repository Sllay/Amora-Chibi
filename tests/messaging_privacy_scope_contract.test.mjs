import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeDiscordMessage } from '../workers/discord/normalize.mjs';
import { normalizeWhatsAppMessage } from '../workers/whatsapp/normalize.mjs';


test('Discord worker emits the Core PrivacyScope enum value exactly', () => {
  const normalized = normalizeDiscordMessage({
    id: 'discord-message-1',
    channel_id: 'discord-channel-1',
    guild_id: 'discord-guild-1',
    timestamp: '2026-11-01T05:55:00Z',
    content: 'oi Amora',
    author: { id: 'discord-user-1', username: 'bk', bot: false },
    mentions: [{ id: 'discord-bot-1' }],
    attachments: [],
  }, { botUserId: 'discord-bot-1' });

  assert.ok(normalized);
  assert.equal(normalized.privacy_scope, 'PERSONAL');
});


test('WhatsApp worker emits the Core PrivacyScope enum value exactly', () => {
  const normalized = normalizeWhatsAppMessage({
    key: {
      id: 'whatsapp-message-1',
      remoteJid: '558199999999@s.whatsapp.net',
      fromMe: false,
    },
    pushName: 'BK',
    messageTimestamp: 1793500000,
    message: { conversation: 'oi Amora' },
  });

  assert.ok(normalized);
  assert.equal(normalized.privacy_scope, 'PERSONAL');
});
