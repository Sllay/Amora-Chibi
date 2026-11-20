import assert from 'node:assert/strict';
import test from 'node:test';
import { collectDiscordIncomingImage, IncomingImageError, MAX_INBOUND_IMAGE_BYTES, MAX_NATIVE_IMAGE_BYTES } from '../workers/discord/incoming-image.mjs';
import { ImageTransformError, transcodeOversizedImage, compositeTransparentImage } from '../workers/discord/incoming-image-transform.mjs';
import { existsSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
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
    fetchImpl: async () => response(Buffer.alloc(MAX_NATIVE_IMAGE_BYTES + 1, 0)),
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

const WEBP = Buffer.from([82, 73, 70, 70, 4, 0, 0, 0, 87, 69, 66, 80, 86, 80, 56, 32]);
const oversizedPng = () => {
  const bytes = Buffer.alloc(MAX_INBOUND_IMAGE_BYTES + 100, 7);
  PNG.copy(bytes, 0);
  return bytes;
};

test('large native PNG gets bounded, aspect-preserving WebP for Core rather than rejected', async () => {
  const native = oversizedPng();
  const uploaded = message();
  uploaded.attachments[0].size = native.length;
  let conversions = 0;
  const image = await collectDiscordIncomingImage(uploaded, {
    fetchImpl: async () => response(native),
    convertImpl: async (bytes, mime) => {
      assert.equal(bytes.length, native.length);
      assert.equal(mime, 'image/png');
      conversions++;
      return { bytes: WEBP, mime: 'image/webp' };
    },
  });
  assert.equal(conversions, 1);
  assert.equal(image.content_mime, 'image/webp');
  assert.deepEqual(Buffer.from(image.content_base64, 'base64'), WEBP);
  assert.equal(image.attachment_id, ATTACHMENT);
});

test('native original takes priority over auto-converted Discord proxy', async () => {
  const uploaded = message();
  uploaded.attachments[0].proxy_url = uploaded.attachments[0].url.replace(
    'cdn.discordapp.com', 'media.discordapp.net'
  );
  const opened = [];
  const image = await collectDiscordIncomingImage(uploaded, {
    fetchImpl: async (url) => { opened.push(url); return response(PNG); },
  });
  assert.deepEqual(opened.length, 1);
  assert.match(opened[0], /^https:\/\/cdn\.discordapp\.com\//);
  assert.equal(image.content_mime, 'image/png');
});

test('safe Discord proxy fallback may transcode PNG attachment to WebP', async () => {
  const uploaded = message();
  uploaded.attachments[0].proxy_url = uploaded.attachments[0].url.replace(
    'cdn.discordapp.com', 'media.discordapp.net'
  );
  let fetches = 0;
  const image = await collectDiscordIncomingImage(uploaded, {
    fetchImpl: async () => (++fetches === 1
      ? new Response('not found', { status: 404 }) : response(WEBP)),
  });
  assert.equal(fetches, 2);
  assert.equal(image.content_mime, 'image/webp');
});

test('reject native CDN payload with substituted MIME despite valid signature', async () => {
  await assert.rejects(() => collectDiscordIncomingImage(message(), {
    fetchImpl: async () => response(WEBP),
  }), (error) => error.code === 'IMAGE_MIME_MISMATCH');
});

test('oversize image failure has safe reason and does not masquerade as image', async () => {
  await assert.rejects(() => collectDiscordIncomingImage(message(), {
    fetchImpl: async () => response(oversizedPng()),
    convertImpl: async () => { throw new ImageTransformError('IMAGE_TRANSFORM_FAILED'); },
  }), (error) => error.code === 'IMAGE_TRANSFORM_FAILED');
  const uploaded = message();
  uploaded.attachments[0].size = MAX_NATIVE_IMAGE_BYTES + 1;
  await assert.rejects(() => collectDiscordIncomingImage(uploaded, {
    fetchImpl: () => { throw new Error('native overlimit must not fetch'); },
  }), (error) => error.code === 'IMAGE_NATIVE_TOO_LARGE');
});

test('private transform artifacts are deleted even when a preset fails', async () => {
  const sources = [];
  const result = await transcodeOversizedImage(oversizedPng(), 'image/png', {
    run: async (args, ms) => {
      assert.ok(ms > 0 && ms <= 6000);
      sources.push(args[0]);
      assert.equal(args[3], '--path');
      const target = args[4].split('[')[0];
      await writeFile(target, WEBP);
    },
  });
  assert.equal(result.mime, 'image/webp');
  assert.deepEqual(result.bytes, WEBP);
  assert.equal(sources.length, 1);
  assert.equal(existsSync(dirname(sources[0])), false);
});

test('image-only overlimit still passes an explicit error code to authenticated Core', async () => {
  const oldFetch = globalThis.fetch;
  const received = [];
  globalThis.fetch = () => { throw new Error('must not fetch oversize'); };
  try {
    const uploaded = message();
    uploaded.content = '';
    uploaded.attachments[0].size = MAX_NATIVE_IMAGE_BYTES + 1;
    const worker = new DiscordWorker({
      token: 'test-token', remoteProcessingAllowed: true,
      core: { inbound: async (input) => {
        received.push(input);
        return { decision: { action: 'context_only' }, outbound: [] };
      } },
    });
    worker.botUserId = '555555555555555555';
    worker.referencedAuthorId = async () => null;
    worker.beginTypingProgress = () => () => {};
    await worker.deliverNormalized(uploaded);
    assert.equal(received.length, 1);
    assert.equal(received[0].incoming_image, undefined);
    assert.equal(received[0].incoming_image_status, 'IMAGE_NATIVE_TOO_LARGE');
  } finally {
    globalThis.fetch = oldFetch;
  }
});


// The PNG header marks an RGBA stream; a spy decoder replaces the native
// libvips stage in unit tests. The container smoke separately verifies bytes.
const alphaPng = () => {
  const bytes = Buffer.alloc(35, 0);
  PNG.subarray(0, 8).copy(bytes, 0);
  bytes.writeUInt32BE(13, 8);
  bytes.write('IHDR', 12, 4, 'ascii');
  bytes[25] = 6; // color type RGBA
  return bytes;
};

test('transparent artwork uses opaque preview, never original alpha URL', async () => {
  const original = alphaPng();
  const uploaded = message();
  uploaded.attachments[0].size = original.length;
  let composites = 0;
  const prepared = await collectDiscordIncomingImage(uploaded, {
    fetchImpl: async () => response(original),
    compositeImpl: async (bytes, mime) => {
      composites += 1;
      assert.deepEqual(bytes, original);
      assert.equal(mime, 'image/png');
      return { mime: 'image/webp', bytes: WEBP };
    },
  });
  assert.equal(composites, 1);
  assert.equal(prepared.content_mime, 'image/webp');
  assert.deepEqual(Buffer.from(prepared.content_base64, 'base64'), WEBP);
  assert.equal(prepared.source_url, undefined);
});

test('transparent oversized original URL is not leaked to image model', async () => {
  const original = Buffer.alloc(MAX_INBOUND_IMAGE_BYTES + 50, 11);
  alphaPng().copy(original);
  const uploaded = message();
  uploaded.attachments[0].size = original.length;
  const prepared = await collectDiscordIncomingImage(uploaded, {
    fetchImpl: async () => response(original),
    convertImpl: async () => ({ bytes: WEBP, mime: 'image/webp' }),
    compositeImpl: async () => ({ bytes: WEBP, mime: 'image/webp' }),
  });
  assert.equal(prepared.source_url, undefined);
});

test('failed transparent preview retains the accompanying caption in Core', async () => {
  const savedFetch = globalThis.fetch;
  const recorded = [];
  globalThis.fetch = async () => response(alphaPng());
  try {
    const worker = new DiscordWorker({
      token: 'test-token', remoteProcessingAllowed: true,
      core: { inbound: async (body) => {
        recorded.push(body);
        return { decision: { action: 'context_only' }, outbound: [] };
      } },
    });
    worker.botUserId = '555555555555555555';
    worker.referencedAuthorId = async () => null;
    worker.beginTypingProgress = () => () => {};
    const textAndImage = message();
    textAndImage.content = 'Amora, esse é o coelho do nosso mapa?';
    textAndImage.attachments[0].size = alphaPng().length;
    await worker.deliverNormalized(textAndImage);
    assert.equal(recorded.length, 1);
    assert.equal(recorded[0].text, textAndImage.content);
    assert.equal(recorded[0].incoming_image, undefined);
    assert.match(recorded[0].incoming_image_status, /^IMAGE_TRANSFORM_/);
  } finally { globalThis.fetch = savedFetch; }
});

test('RGB flatten stage is bounded and erases disposable image files', async () => {
  const paths = [];
  const result = await compositeTransparentImage(alphaPng(), 'image/png', {
    run: async (args) => {
      assert.deepEqual(args.slice(0, 1), ['flatten']);
      assert.equal(args[3], '--background');
      assert.equal(args[4], '230');
      paths.push(args[1]);
      await writeFile(args[2].split('[')[0], WEBP);
    },
  });
  assert.equal(result.mime, 'image/webp');
  assert.deepEqual(result.bytes, WEBP);
  assert.ok(paths.length === 1);
  assert.equal(existsSync(dirname(paths[0])), false);
});
