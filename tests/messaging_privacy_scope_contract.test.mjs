import assert from 'node:assert/strict';
import test from 'node:test';

import { MessagingCoreClient } from '../workers/common/core-client.mjs';
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


test('presence control keeps at least five seconds after the Discord interaction is deferred', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  let observedTimeout = null;

  globalThis.setTimeout = (_callback, timeoutMs) => {
    observedTimeout = timeoutMs;
    return Symbol('timer');
  };
  globalThis.clearTimeout = () => {};
  globalThis.fetch = async () => new Response(JSON.stringify({ response_text: 'ok' }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

  try {
    const client = new MessagingCoreClient({
      baseUrl: 'https://core.example.test',
      token: 'm'.repeat(48),
      platform: 'discord',
    });
    await client.presenceControl({
      action: 'status',
      senderIdentifiers: [{
        identifier_type: 'discord_user_id',
        external_actor_id: 'discord-user-1',
        confidence: 1.0,
      }],
      timeoutMs: 1800,
    });
    assert.equal(observedTimeout, 5000);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});
