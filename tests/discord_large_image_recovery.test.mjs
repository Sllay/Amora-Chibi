import assert from 'node:assert/strict';
import test from 'node:test';
import {
  collectDiscordIncomingImage, MAX_NATIVE_IMAGE_BYTES, MAX_INBOUND_IMAGE_BYTES,
} from '../workers/discord/incoming-image.mjs';
import { DiscordWorker } from '../workers/discord/worker.mjs';

const CHANNEL = '123456789012345678';
const ATTACHMENT = '987654321098765432';
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 13, 10, 26, 10, 1, 2, 3, 4, 5, 6]);
const WEBP = Buffer.from([82, 73, 70, 70, 4, 0, 0, 0, 87, 69, 66, 80, 86, 80, 56, 32]);

function photo({ mime = 'image/png', filename = 'foto.png', bytes = PNG } = {}) {
  const url = `https://cdn.discordapp.com/attachments/${CHANNEL}/${ATTACHMENT}/${filename}?ex=abc`;
  return {
    id: '111111111111111111', channel_id: CHANNEL, guild_id: null,
    content: '', author: { id: '222222222222222222', bot: false },
    timestamp: '2026-11-16T10:00:00Z', mentions: [],
    attachments: [{ id: ATTACHMENT, url, content_type: mime, filename, size: bytes.length }],
  };
}

function fakeResponse(bytes) {
  return new Response(bytes, {
    status: 200, headers: { 'content-length': String(bytes.length) },
  });
}

function fakeWorker(core) {
  const worker = new DiscordWorker({
    token: 'test-token', core, remoteProcessingAllowed: true,
  });
  worker.botUserId = '555555555555555555';
  worker.referencedAuthorId = async () => null;
  worker.beginTypingProgress = () => () => {};
  return worker;
}

function avifHeader(brand) {
  const bytes = Buffer.alloc(20);
  bytes.writeUInt32BE(20, 0);
  bytes.write('ftyp', 4);
  bytes.write(brand, 8);
  bytes.writeUInt32BE(0, 12);
  bytes.write(brand, 16);
  return bytes;
}

test('native PNG above Groq 20 MB limit is boundedly converted to WebP', async () => {
  const native = Buffer.alloc(MAX_INBOUND_IMAGE_BYTES + 100, 0x31);
  PNG.copy(native, 0);
  assert.ok(native.length < MAX_NATIVE_IMAGE_BYTES);
  const stages = [];
  let receivedBytes = 0;
  const result = await collectDiscordIncomingImage(photo({ bytes: native }), {
    fetchImpl: async () => fakeResponse(native),
    onStage: (stage) => stages.push(stage),
    convertImpl: async (bytes, mime) => {
      receivedBytes = bytes.length;
      assert.equal(mime, 'image/png');
      return { bytes: WEBP, mime: 'image/webp' };
    },
  });
  assert.equal(receivedBytes, native.length);
  assert.equal(result.content_mime, 'image/webp');
  assert.equal(result.attachment_id, ATTACHMENT);
  assert.deepEqual(stages, [
    'candidate', 'original_fetch', 'downloaded',
    'transcode_started', 'transcode_completed', 'ready',
  ]);
});

test('10 MB native PNG stays full fidelity without transcoding', async () => {
  const native = Buffer.alloc(10_000_000, 0x31);
  PNG.copy(native, 0);
  const out = await collectDiscordIncomingImage(photo({ bytes: native }), {
    fetchImpl: async () => fakeResponse(native),
    convertImpl: async () => { throw new Error('10 MB should not be transcoded'); },
  });
  assert.equal(out.content_mime, 'image/png');
  assert.deepEqual(Buffer.from(out.content_base64, 'base64'), native);
});

test('native photo without Discord content_type is sniffed rather than silently ignored', async () => {
  const input = photo({ mime: null });
  const worker = fakeWorker({ inbound: async (p) => {
    assert.equal(p.attachments[0].media_type, 'image/png');
    assert.equal(p.incoming_image.content_mime, 'image/png');
    return { decision: { action: 'context_only' }, outbound: [] };
  } });
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => fakeResponse(PNG);
  try {
    await worker.deliverNormalized(input);
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test('native small AVIF and HEIC are still converted to provider-safe WebP', async () => {
  for (const [brand, mime, filename] of [
    ['avif', 'image/avif', 'photo.avif'],
    ['heic', 'image/heic', 'photo.heic'],
  ]) {
    const native = avifHeader(brand);
    let called = 0;
    const out = await collectDiscordIncomingImage(
      photo({ mime, filename, bytes: native }),
      {
        fetchImpl: async () => fakeResponse(native),
        convertImpl: async (bytes, observedMime) => {
          called++;
          assert.deepEqual(bytes, native);
          assert.equal(observedMime, mime);
          return { bytes: WEBP, mime: 'image/webp' };
        },
      },
    );
    assert.equal(called, 1);
    assert.equal(out.content_mime, 'image/webp');
  }
});

test('unrecognized native image type propagates unavailable status instead of fake observation', async () => {
  const input = photo({ mime: 'image/gif', filename: 'animado.gif' });
  const worker = fakeWorker({ inbound: async (p) => {
    assert.equal(p.incoming_image, null);
    assert.equal(p.incoming_image_status, 'IMAGE_UNSUPPORTED_FORMAT');
    return { decision: { action: 'context_only' }, outbound: [] };
  } });
  await worker.deliverNormalized(input);
});

test('observed HTTP 413 triggers exactly one smaller visual retry', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => fakeResponse(PNG);
  const received = [];
  try {
    const worker = fakeWorker({ inbound: async (p) => {
      received.push(structuredClone(p));
      if (received.length === 1) {
        const error = new Error('413');
        error.status = 413;
        error.code = 'CORE_HTTP_413';
        throw error;
      }
      return { decision: { action: 'context_only' }, outbound: [] };
    } });
    let reductions = 0;
    worker.recompressIncomingImage = async (bytes, mime, options) => {
      reductions++;
      assert.deepEqual(bytes, PNG);
      assert.equal(mime, 'image/png');
      assert.equal(options.maxBytes, 640 * 1024);
      return { bytes: WEBP, mime: 'image/webp' };
    };
    await worker.deliverNormalized(photo());
    assert.equal(reductions, 1);
    assert.equal(received.length, 2);
    assert.equal(received[0].incoming_image.content_mime, 'image/png');
    assert.equal(received[1].incoming_image.content_mime, 'image/webp');
    assert.equal(received[1].message_id, received[0].message_id);
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test('repeated HTTP 413 falls back to metadata-only without losing the user turn', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => fakeResponse(PNG);
  const received = [];
  try {
    const worker = fakeWorker({ inbound: async (p) => {
      received.push(structuredClone(p));
      if (received.length <= 2) {
        const error = new Error('413');
        error.status = 413;
        error.code = 'CORE_HTTP_413';
        throw error;
      }
      return { decision: { action: 'respond' }, outbound: [] };
    } });
    worker.recompressIncomingImage = async () => ({ bytes: WEBP, mime: 'image/webp' });
    await worker.deliverNormalized(photo());
    assert.equal(received.length, 3);
    assert.equal(received[2].incoming_image, undefined);
    assert.equal(received[2].incoming_image_status, 'IMAGE_TRANSPORT_TOO_LARGE');
    assert.equal(received[2].message_id, received[0].message_id);
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test('recompression failure on HTTP 413 still delivers metadata-only', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => fakeResponse(PNG);
  const received = [];
  try {
    const worker = fakeWorker({ inbound: async (p) => {
      received.push(structuredClone(p));
      if (received.length === 1) throw Object.assign(new Error('413'), { status: 413 });
      return { decision: { action: 'respond' }, outbound: [] };
    } });
    worker.recompressIncomingImage = async () => { throw new Error('synthetic codec failure'); };
    await worker.deliverNormalized(photo());
    assert.equal(received.length, 2);
    assert.equal(received[1].incoming_image_status, 'IMAGE_TRANSPORT_TOO_LARGE');
  } finally {
    globalThis.fetch = oldFetch;
  }
});
