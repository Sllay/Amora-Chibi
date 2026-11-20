import test from 'node:test';
import assert from 'node:assert/strict';

import {
  publicScreenshotTarget, WebScreenshotService, screenshotMultipart,
} from '../workers/discord/web-screenshot.mjs';
import { DiscordRestClient } from '../workers/discord/http.mjs';
import { DiscordWorker } from '../workers/discord/worker.mjs';

const IMAGE_URL = 'https://cdn.microlink.io/amora-screenshot.jpg';
const IMAGE_BYTES = new Uint8Array([0xff, 0xd8, 0xff, 0xe1, 0, 1, 2, 3, 4, 5]);

function apiResponse() {
  const doc = {
    status: 'success', data: {
      screenshot: { url: IMAGE_URL, type: 'jpeg', size: IMAGE_BYTES.byteLength },
    },
  };
  return new Response(JSON.stringify(doc), {
    status: 200, headers: { 'content-type': 'application/json' },
  });
}

function imageResponse() {
  return new Response(IMAGE_BYTES, {
    status: 200, headers: { 'content-type': 'image/jpeg' },
  });
}

test('web screenshots accept public site URL but reject loopback, secrets and odd protocols', () => {
  assert.equal(
    publicScreenshotTarget('https://godotengine.org/article/android'),
    'https://godotengine.org/article/android',
  );
  for (const url of [
    'http://127.0.0.1/admin', 'http://10.0.0.1/admin',
    'http://localhost/admin', 'http://metadata.google.internal/meta',
    'https://example.org/?access_token=secret',
    'https://example.org/?api_key=secret',
    'https://user:pass@example.org/page',
    'file:///etc/passwd', 'ftp://example.org/page',
  ]) assert.equal(publicScreenshotTarget(url), null, url);
});

test('keyless Microlink delivers a bounded real raster and no site cookies or private data', async () => {
  const called = [];
  const fakeFetch = async (url, options) => {
    called.push({ url, options });
    if (called.length === 1) return apiResponse();
    if (called.length === 2) return imageResponse();
    throw new Error('Unexpected request');
  };
  const service = new WebScreenshotService({ fetchImpl: fakeFetch });
  const image = await service.capture('https://godotengine.org/article/android');
  assert.equal(image.mime, 'image/jpeg');
  assert.equal(image.filename, 'amora-web-print.jpg');
  assert.deepEqual(image.bytes, IMAGE_BYTES);
  const api = new URL(called[0].url);
  assert.equal(api.hostname, 'api.microlink.io');
  assert.equal(api.searchParams.get('url'), 'https://godotengine.org/article/android');
  assert.equal(api.searchParams.get('screenshot'), 'true');
  assert.equal(called[0].options.redirect, 'error');
  assert.equal(called[1].url, IMAGE_URL);
  assert.equal(called[1].options.redirect, 'error');
  assert.equal(JSON.stringify(called).includes('PERSON_BK'), false);
});

test('renderer rejects arbitrary screenshot image hosts and non-raster bodies', async () => {
  const fakeFetch = async (url) => url.startsWith('https://api.microlink.io/')
    ? new Response(JSON.stringify({
      status: 'success', data: { screenshot: { url: 'https://127.0.0.1/img.png' } },
    }), { status: 200, headers: { 'content-type': 'application/json' } })
    : imageResponse();
  assert.equal(await new WebScreenshotService({ fetchImpl: fakeFetch }).capture('https://godotengine.org/'), null);
  const invalidImage = async (url) => url.startsWith('https://api.microlink.io/')
    ? apiResponse() : new Response('not an image', {
      status: 200, headers: { 'content-type': 'image/jpeg' },
    });
  assert.equal(await new WebScreenshotService({ fetchImpl: invalidImage }).capture('https://godotengine.org/'), null);
});

test('screenshot quota cannot be exceeded by consecutive requests', async () => {
  const calls = [];
  const service = new WebScreenshotService({
    dailyLimit: 1,
    fetchImpl: async (url) => {
      calls.push(url);
      return url.startsWith('https://api.microlink.io/') ? apiResponse() : imageResponse();
    },
  });
  assert.ok(await service.capture('https://godotengine.org/'));
  assert.equal(await service.capture('https://godotengine.org/article'), null);
  assert.equal(calls.length, 2);
});

test('multipart Discord message includes attached image and keeps normal text', async () => {
  const payload = { content: 'Veja a fonte https://godotengine.org', allowed_mentions: { parse: [] } };
  const form = screenshotMultipart(payload, {
    bytes: IMAGE_BYTES, filename: 'amora-web-print.jpg', mime: 'image/jpeg',
  });
  const envelope = JSON.parse(form.get('payload_json'));
  assert.equal(envelope.content, payload.content);
  assert.deepEqual(envelope.attachments, [{
    id: 0, filename: 'amora-web-print.jpg', description: 'Captura da página web',
  }]);
  assert.equal(form.get('files[0]').type, 'image/jpeg');
  assert.equal(form.get('files[0]').size, IMAGE_BYTES.length);
});

test('REST client leaves multipart boundary to native FormData and keeps Bot auth', async () => {
  const sent = [];
  const rest = new DiscordRestClient({
    token: 'test-bot-token',
    maxAttempts: 1,
    fetchImpl: async (url, options) => {
      sent.push({ url, options });
      return new Response(JSON.stringify({ id: '123', channel_id: 'chan' }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    },
  });
  const form = screenshotMultipart({ content: 'hello' }, {
    bytes: IMAGE_BYTES, filename: 'amora-web-print.jpg', mime: 'image/jpeg',
  });
  const result = await rest.request('/channels/chan/messages', {
    method: 'POST', multipart: form,
  });
  assert.equal(result.id, '123');
  assert.equal(sent[0].options.body, form);
  assert.equal(sent[0].options.headers.Authorization, 'Bot test-bot-token');
  assert.equal(sent[0].options.headers['Content-Type'], undefined);
});

test('Discord worker attaches screenshot ONLY for verified Core owner metadata', async () => {
  const calls = [];
  const screenshots = [];
  const worker = new DiscordWorker({
    token: 'test',
    core: {},
    rest: {
      request: async (path, options) => {
        calls.push(options);
        return { id: 'sent', channel_id: 'chan',
          attachments: options.multipart
            ? [{ id: 'screenshot-id', filename: 'amora-web-print.jpg' }]
            : [] };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  worker.webScreenshots = {
    async capture(url) {
      screenshots.push(url);
      return { mime: 'image/jpeg', filename: 'amora-web-print.jpg', bytes: IMAGE_BYTES };
    },
  };
  const base = {
    id: '12345678-abcd-abcd-abcd-1234567890ab',
    platform: 'discord',
    conversation_id: 'chan',
    text: 'Fontes da busca:\nhttps://godotengine.org/article/android',
    metadata: { web_capture_source: 'https://godotengine.org/article/android' },
  };
  await worker.sendOutbound(base);
  assert.equal(screenshots.length, 0);
  assert.ok(calls[0].body);
  assert.equal(calls[0].multipart, undefined);

  await worker.sendOutbound({
    ...base,
    metadata: { ...base.metadata, web_capture_owner_verified: true,
      web_capture_requested: true },
  });
  assert.deepEqual(screenshots, ['https://godotengine.org/article/android']);
  assert.ok(calls[1].multipart instanceof FormData);
  const payload = JSON.parse(calls[1].multipart.get('payload_json'));
  assert.match(payload.content, /godotengine\.org/);
  assert.equal(payload.allowed_mentions.replied_user, false);
});

test('failed external screenshot never prevents sending research links', async () => {
  const calls = [];
  const worker = new DiscordWorker({
    token: 'test',
    core: {},
    rest: {
      request: async (path, options) => {
        calls.push(options);
        return { id: 'sent', channel_id: 'chan' };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  worker.webScreenshots = { capture: async () => null };
  await worker.sendOutbound({
    id: '12345678-abcd-abcd-abcd-1234567890ab', platform: 'discord',
    conversation_id: 'chan', text: 'https://godotengine.org/article/android',
    metadata: { web_capture_owner_verified: true, web_capture_requested: true,
                web_capture_source: 'https://godotengine.org/article/android' },
  });
  assert.ok(calls[0].body);
  assert.match(calls[0].body.content, /godotengine\.org/);
  assert.match(calls[0].body.content, /não consegui capturar o print/i);
});

test('Discord image upload 400 degrades to the same verified links as JSON', async () => {
  const calls = [];
  const worker = new DiscordWorker({
    token: 'test', core: {},
    rest: {
      request: async (path, options) => {
        calls.push(options);
        if (options.multipart) {
          const error = new Error('Attachment denied');
          error.status = 400;
          error.retryable = false;
          throw error;
        }
        return { id: 'sent', channel_id: 'chan' };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  worker.webScreenshots = { capture: async () => ({
    bytes: IMAGE_BYTES, mime: 'image/jpeg', filename: 'amora-web-print.jpg',
  }) };
  const id = await worker.sendOutbound({
    id: '12345678-abcd-abcd-abcd-1234567890ab', platform: 'discord',
    conversation_id: 'chan', text: 'https://godotengine.org/article/android',
    metadata: { web_capture_owner_verified: true, web_capture_requested: true,
      web_capture_source: 'https://godotengine.org/article/android' },
  });
  assert.equal(id, 'sent');
  assert.equal(calls.length, 2);
  assert.ok(calls[0].multipart);
  assert.match(calls[1].body.content, /godotengine\.org/);
  assert.equal(calls[1].body.nonce, JSON.parse(calls[0].multipart.get('payload_json')).nonce);
});

test('Microlink regional CDN host iad.microlink.io is supported', async () => {
  const regional = 'https://iad.microlink.io/example-screenshot.jpg';
  const requested = [];
  const service = new WebScreenshotService({
    fetchImpl: async (url) => {
      requested.push(url);
      return requested.length === 1
        ? new Response(JSON.stringify({ status: 'success', data: { screenshot: { url: regional } } }), { status: 200 })
        : imageResponse();
    },
  });
  const photo = await service.capture('https://godotengine.org/');
  assert.ok(photo);
  assert.equal(requested[1], regional);
});


test('Microlink official S3 screenshot bucket is accepted without permitting generic S3', async () => {
  // Official Microlink docs show screenshot assets at this exact bucket.
  const official = 'https://microlink-cdn.s3.amazonaws.com/s/sample/screenshot.jpg';
  const calls = [];
  const capture = new WebScreenshotService({
    fetchImpl: async (url) => {
      calls.push(url);
      return calls.length === 1
        ? new Response(JSON.stringify({
          status: 'success', data: { screenshot: { url: official } },
        }), { status: 200 })
        : imageResponse();
    },
  });
  assert.ok(await capture.capture('https://godotengine.org/'));
  assert.equal(calls[1], official);

  for (const untrusted of [
    'https://other-bucket.s3.amazonaws.com/s/screenshot.jpg',
    'https://s3.amazonaws.com/microlink-cdn/s/screenshot.jpg',
    'https://microlink-cdn.s3.amazonaws.com.attacker.test/s/screenshot.jpg',
    'http://microlink-cdn.s3.amazonaws.com/s/screenshot.jpg',
    'https://user:password@microlink-cdn.s3.amazonaws.com/s/screenshot.jpg',
  ]) {
    const seen = [];
    const renderer = new WebScreenshotService({
      fetchImpl: async (url) => {
        seen.push(url);
        return new Response(JSON.stringify({
          status: 'success', data: { screenshot: { url: untrusted } },
        }), { status: 200 });
      },
    });
    assert.equal(await renderer.capture('https://godotengine.org/'), null, untrusted);
    assert.equal(seen.length, 1, 'Must not follow untrusted CDN URL');
  }
});


test('verified screenshot upload requires explicit capture request metadata', async () => {
  const calls = [];
  const worker = new DiscordWorker({
    token: 'test', core: {},
    rest: {
      request: async (path, options) => {
        calls.push(options);
        return { id: 'sent', channel_id: 'chan' };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  let captures = 0;
  worker.webScreenshots = { capture: async () => { captures += 1; return null; } };
  await worker.sendOutbound({
    id: '12345678-abcd-abcd-abcd-1234567890ab',
    platform: 'discord', conversation_id: 'chan',
    text: 'Pesquisa concluída.',
    metadata: { web_capture_requested: true },
  });
  assert.equal(captures, 0);
  assert.match(calls[0].body.content, /Nenhum anexo foi enviado/i);
});

test('missing attachment in a successful Discord response gets an explicit non-delivery notice', async () => {
  const calls = [];
  const worker = new DiscordWorker({
    token: 'test', core: {},
    rest: {
      request: async (path, options) => {
        calls.push(options);
        return { id: 'sent-' + calls.length, channel_id: 'chan', attachments: [] };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  worker.webScreenshots = {
    capture: async () => ({
      bytes: IMAGE_BYTES, mime: 'image/jpeg', filename: 'amora-web-print.jpg',
    }),
  };
  await worker.sendOutbound({
    id: '12345678-abcd-abcd-abcd-1234567890ab',
    platform: 'discord', conversation_id: 'chan', text: 'Link público consultado.',
    metadata: {
      web_capture_requested: true,
      web_capture_owner_verified: true,
      web_capture_source: 'https://godotengine.org/',
    },
  });
  assert.equal(calls.length, 2);
  assert.ok(calls[0].multipart instanceof FormData);
  assert.match(calls[1].body.content, /não consegui confirmar o anexo/i);
  assert.notEqual(calls[1].body.nonce,
    JSON.parse(calls[0].multipart.get('payload_json')).nonce);
});


test('verified screenshot source from previous Core remains supported during rolling deploy', async () => {
  const sent = [];
  const worker = new DiscordWorker({
    token: 'test', core: {},
    rest: {
      request: async (_, options) => {
        sent.push(options);
        return { id: 'sent', channel_id: 'chan',
          attachments: options.multipart
            ? [{ id: 'image', filename: 'amora-web-print.jpg' }] : [] };
      },
    },
    runtime: { rememberBotMessage() {} },
  });
  worker.webScreenshots = { capture: async () => ({
    bytes: IMAGE_BYTES, mime: 'image/jpeg', filename: 'amora-web-print.jpg',
  }) };
  await worker.sendOutbound({
    id: '12345678-abcd-abcd-abcd-1234567890ab',
    platform: 'discord', conversation_id: 'chan', text: 'Segue a fonte.',
    metadata: {
      web_capture_owner_verified: true,
      web_capture_source: 'https://godotengine.org/',
    },
  });
  assert.equal(sent.length, 1);
  assert.ok(sent[0].multipart instanceof FormData);
});
