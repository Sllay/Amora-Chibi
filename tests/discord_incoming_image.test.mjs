import assert from 'node:assert/strict';
import test from 'node:test';
import { collectDiscordIncomingImage, IncomingImageError, MAX_INBOUND_IMAGE_BYTES } from '../workers/discord/incoming-image.mjs';
import { DiscordWorker } from '../workers/discord/worker.mjs';

const CHANNEL = '123456789012345678';
const ATTACHMENT = '987654321098765432';
const AUTHOR = '222222222222222222';
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 13, 10, 26, 10, 1, 2, 3, 4, 5, 6]);

const message = (url = `https://cdn.discordapp.com/attachments/${CHANNEL}/${ATTACHMENT}/foto.png?ex=abc&is=abc&hm=abc`) => ({
  id: '111111111111111111', channel_id: CHANNEL,
  author: { id: AUTHOR, bot: false, username: 'user' },
  content: 'o que tem aqui?', guild_id: null, mentions: [],
  timestamp: '2026-11-16T10:00:00Z',
  attachments: [{ id: ATTACHMENT, url, content_type: 'image/png', filename: 'foto.png', size: PNG.length }],
});
const response = (bytes = PNG) => new Response(bytes, {
  status: 200, headers: { 'content-type': 'image/png', 'content-length': String(bytes.length) },
});

test('extract native Discord image bytes with strict CDN host, path and MIME', async () => {
  const item = await collectDiscordIncomingImage(message(), {
    fetchImpl: async (url, options) => {
      assert.match(url, /^https:\/\/cdn\.discordapp\.com\/attachments\//);
      assert.equal(options.redirect, 'error');
      return response();
    },
  });
  assert.equal(item.attachment_id, ATTACHMENT);
  assert.equal(item.content_mime, 'image/png');
  assert.deepEqual(Buffer.from(item.content_base64, 'base64'), PNG);
});

test('reject external, redirect-like, mismatched attachment and non-https URLs before fetch', async () => {
  const bad = [
    `https://evil.test/attachments/${CHANNEL}/${ATTACHMENT}/foto.png`,
    `http://cdn.discordapp.com/attachments/${CHANNEL}/${ATTACHMENT}/foto.png`,
    `https://cdn.discordapp.com.evil.test/attachments/${CHANNEL}/${ATTACHMENT}/foto.png`,
    `https://cdn.discordapp.com/attachments/${CHANNEL}/111111111111111111/foto.png`,
    `https://cdn.discordapp.com@evil.test/attachments/${CHANNEL}/${ATTACHMENT}/foto.png`,
  ];
  for (const url of bad) {
    await assert.rejects(() => collectDiscordIncomingImage(message(url), {
      fetchImpl: () => { throw new Error('should never fetch'); },
    }), IncomingImageError);
  }
});

test('enforce streaming byte cap, reject content substitution, and never inspect group images', async () => {
  await assert.rejects(() => collectDiscordIncomingImage(message(), {
    fetchImpl: async () => response(Buffer.alloc(MAX_INBOUND_IMAGE_BYTES + 1, 0)),
  }), (error) => error.code === 'IMAGE_TOO_LARGE');
  await assert.rejects(() => collectDiscordIncomingImage(message(), {
    fetchImpl: async () => response(Buffer.from('not an image')),
  }), (error) => error.code === 'IMAGE_SIGNATURE_INVALID');
  const group = { ...message(), guild_id: '666666666666666666' };
  assert.equal(await collectDiscordIncomingImage(group, {
    fetchImpl: () => { throw new Error('group must not fetch'); },
  }), null);
});

test('Discord inbound sends ephemeral image only when remote consent is enabled for a DM', async () => {
  const oldFetch = globalThis.fetch;
  const received = [];
  globalThis.fetch = async () => response();
  try {
    const worker = new DiscordWorker({
      token: 'test-token', core: {
        inbound: async (payload) => {
          received.push(payload);
          return { decision: { action: 'context_only', reason_codes: [] }, outbound: [] };
        },
      }, remoteProcessingAllowed: true,
    });
    worker.botUserId = '555555555555555555';
    worker.referencedAuthorId = async () => null;
    worker.beginTypingProgress = () => () => {};
    await worker.deliverNormalized(message());
    assert.equal(received.length, 1);
    assert.equal(received[0].incoming_image.attachment_id, ATTACHMENT);
    assert.equal(received[0].incoming_image.content_mime, 'image/png');

    const noConsent = new DiscordWorker({
      token: 'test-token', core: { inbound: async (p) => {
        received.push(p);
        return { decision: { action: 'context_only', reason_codes: [] }, outbound: [] };
      } }, remoteProcessingAllowed: false,
    });
    noConsent.botUserId = worker.botUserId;
    noConsent.referencedAuthorId = async () => null;
    noConsent.beginTypingProgress = () => () => {};
    await noConsent.deliverNormalized(message());
    assert.equal(received.length, 2);
    assert.equal(received[1].incoming_image, undefined);
  } finally {
    globalThis.fetch = oldFetch;
  }
});
