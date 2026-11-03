import assert from 'node:assert/strict';
import test from 'node:test';

import {
  StickerMediaError,
  fetchDiscordStickerMedia,
  resolveDiscordStickerMedia,
} from '../workers/discord/sticker-media.mjs';

const STICKER_ID = '123456789012345678';
const PNG_SIGNATURE = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function responseLike(bytes, { contentType, contentLength, url = '', status = 200 } = {}) {
  const headers = new Headers();
  if (contentType) headers.set('content-type', contentType);
  if (contentLength !== undefined) headers.set('content-length', String(contentLength));
  const response = new Response(bytes, { status, headers });
  return url
    ? {
        ok: response.ok,
        status: response.status,
        headers: response.headers,
        body: response.body,
        arrayBuffer: response.arrayBuffer.bind(response),
        url,
      }
    : response;
}

async function rejectsCode(promiseFactory, code) {
  await assert.rejects(
    promiseFactory,
    (error) => error instanceof StickerMediaError && error.code === code,
  );
}

test('resolver derives only canonical Discord sticker endpoints', () => {
  assert.deepEqual(resolveDiscordStickerMedia({ nativeId: STICKER_ID, formatType: 1 }), {
    stickerId: STICKER_ID,
    formatType: 1,
    url: `https://cdn.discordapp.com/stickers/${STICKER_ID}.png`,
    expectedKind: 'png',
    expectedMime: 'image/png',
  });
  assert.deepEqual(resolveDiscordStickerMedia({ nativeId: STICKER_ID, formatType: 2 }), {
    stickerId: STICKER_ID,
    formatType: 2,
    url: `https://cdn.discordapp.com/stickers/${STICKER_ID}.png`,
    expectedKind: 'apng',
    expectedMime: 'image/png',
  });
  assert.deepEqual(resolveDiscordStickerMedia({ nativeId: STICKER_ID, formatType: 3 }), {
    stickerId: STICKER_ID,
    formatType: 3,
    url: `https://cdn.discordapp.com/stickers/${STICKER_ID}.json`,
    expectedKind: 'lottie',
    expectedMime: 'application/json',
  });
  assert.deepEqual(resolveDiscordStickerMedia({ nativeId: STICKER_ID, formatType: 4 }), {
    stickerId: STICKER_ID,
    formatType: 4,
    url: `https://media.discordapp.net/stickers/${STICKER_ID}.gif`,
    expectedKind: 'gif',
    expectedMime: 'image/gif',
  });
});

test('resolver rejects arbitrary URL-shaped ids and unsupported formats', () => {
  assert.throws(
    () => resolveDiscordStickerMedia({ nativeId: 'https://evil.test/sticker.png', formatType: 1 }),
    (error) => error instanceof StickerMediaError && error.code === 'INVALID_STICKER_ID',
  );
  assert.throws(
    () => resolveDiscordStickerMedia({ nativeId: 'abc123', formatType: 1 }),
    (error) => error instanceof StickerMediaError && error.code === 'INVALID_STICKER_ID',
  );
  assert.throws(
    () => resolveDiscordStickerMedia({ nativeId: STICKER_ID, formatType: 9 }),
    (error) => error instanceof StickerMediaError && error.code === 'UNSUPPORTED_STICKER_FORMAT',
  );
});

test('fetch accepts a bounded static PNG and forces redirect:error', async () => {
  const bytes = Uint8Array.from([...PNG_SIGNATURE, 0, 0, 0, 0]);
  let seenUrl = null;
  let seenOptions = null;
  const result = await fetchDiscordStickerMedia(
    { nativeId: STICKER_ID, formatType: 1 },
    {
      fetchImpl: async (url, options) => {
        seenUrl = url;
        seenOptions = options;
        return responseLike(bytes, { contentType: 'image/png' });
      },
    },
  );

  assert.equal(seenUrl, `https://cdn.discordapp.com/stickers/${STICKER_ID}.png`);
  assert.equal(seenOptions.redirect, 'error');
  assert.equal(seenOptions.method, 'GET');
  assert.equal(result.mime, 'image/png');
  assert.deepEqual(result.bytes, bytes);
});

test('fetch accepts APNG only when animation control chunk is present', async () => {
  const marker = new TextEncoder().encode('acTL');
  const valid = Uint8Array.from([...PNG_SIGNATURE, 0, 0, ...marker, 0, 0]);
  const result = await fetchDiscordStickerMedia(
    { nativeId: STICKER_ID, formatType: 2 },
    { fetchImpl: async () => responseLike(valid, { contentType: 'image/png' }) },
  );
  assert.equal(result.formatType, 2);

  const invalid = Uint8Array.from([...PNG_SIGNATURE, 0, 0, 0, 0]);
  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 2 },
      { fetchImpl: async () => responseLike(invalid, { contentType: 'image/png' }) },
    ),
    'INVALID_STICKER_CONTENT',
  );
});

test('fetch validates GIF magic bytes', async () => {
  const gif = new TextEncoder().encode('GIF89a\x01\x00\x01\x00');
  const result = await fetchDiscordStickerMedia(
    { nativeId: STICKER_ID, formatType: 4 },
    { fetchImpl: async () => responseLike(gif, { contentType: 'image/gif' }) },
  );
  assert.equal(result.mime, 'image/gif');

  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 4 },
      { fetchImpl: async () => responseLike(new TextEncoder().encode('not-a-gif'), { contentType: 'image/gif' }) },
    ),
    'INVALID_STICKER_CONTENT',
  );
});

test('fetch structurally validates Lottie JSON', async () => {
  const lottie = new TextEncoder().encode(JSON.stringify({
    v: '5.7.4',
    fr: 30,
    ip: 0,
    op: 60,
    w: 320,
    h: 320,
    layers: [],
  }));
  const result = await fetchDiscordStickerMedia(
    { nativeId: STICKER_ID, formatType: 3 },
    { fetchImpl: async () => responseLike(lottie, { contentType: 'application/json' }) },
  );
  assert.equal(result.mime, 'application/json');

  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 3 },
      { fetchImpl: async () => responseLike(new TextEncoder().encode('{"layers":[]}'), { contentType: 'application/json' }) },
    ),
    'INVALID_STICKER_CONTENT',
  );
});

test('fetch rejects MIME that conflicts with the derived format', async () => {
  const bytes = Uint8Array.from([...PNG_SIGNATURE, 0, 0]);
  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 1 },
      { fetchImpl: async () => responseLike(bytes, { contentType: 'text/html' }) },
    ),
    'UNEXPECTED_STICKER_MIME',
  );
});

test('fetch rejects oversized declared content before consuming the body', async () => {
  let bodyRead = false;
  const response = {
    ok: true,
    status: 200,
    url: '',
    headers: new Headers({
      'content-type': 'image/png',
      'content-length': String(2048),
    }),
    body: null,
    async arrayBuffer() {
      bodyRead = true;
      return Uint8Array.from([...PNG_SIGNATURE]).buffer;
    },
  };

  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 1 },
      { fetchImpl: async () => response, maxBytes: 1024 },
    ),
    'STICKER_TOO_LARGE',
  );
  assert.equal(bodyRead, false);
});

test('fetch aborts a streamed response that crosses the byte limit', async () => {
  const chunk = new Uint8Array(800);
  chunk.set(PNG_SIGNATURE);
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(chunk);
      controller.enqueue(new Uint8Array(800));
      controller.close();
    },
  });
  const response = new Response(stream, {
    status: 200,
    headers: { 'content-type': 'image/png' },
  });

  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 1 },
      { fetchImpl: async () => response, maxBytes: 1024 },
    ),
    'STICKER_TOO_LARGE',
  );
});

test('fetch rejects a final URL that escaped the derived Discord endpoint', async () => {
  const bytes = Uint8Array.from([...PNG_SIGNATURE, 0, 0]);
  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 1 },
      {
        fetchImpl: async () => responseLike(bytes, {
          contentType: 'image/png',
          url: 'https://evil.example/stickers/pwn.png',
        }),
      },
    ),
    'STICKER_FETCH_REDIRECTED',
  );
});

test('fetch turns AbortController timeout into a stable error code', async () => {
  await rejectsCode(
    () => fetchDiscordStickerMedia(
      { nativeId: STICKER_ID, formatType: 1 },
      {
        timeoutMs: 100,
        fetchImpl: (_url, { signal }) => new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => {
            const error = new Error('aborted');
            error.name = 'AbortError';
            reject(error);
          }, { once: true });
        }),
      },
    ),
    'STICKER_FETCH_TIMEOUT',
  );
});
