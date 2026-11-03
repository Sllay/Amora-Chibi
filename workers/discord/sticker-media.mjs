const DISCORD_CDN_HOST = 'cdn.discordapp.com';
const DISCORD_MEDIA_HOST = 'media.discordapp.net';
const DEFAULT_TIMEOUT_MS = 8_000;
const DEFAULT_MAX_BYTES = 1024 * 1024;
const MAX_STICKER_ID_LENGTH = 24;

const PNG_SIGNATURE = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const GIF87A = Uint8Array.from([0x47, 0x49, 0x46, 0x38, 0x37, 0x61]);
const GIF89A = Uint8Array.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]);

export class StickerMediaError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'StickerMediaError';
    this.code = code;
  }
}

function assertStickerId(nativeId) {
  const value = String(nativeId ?? '').trim();
  if (!/^[0-9]{1,24}$/.test(value) || value.length > MAX_STICKER_ID_LENGTH) {
    throw new StickerMediaError('INVALID_STICKER_ID', 'Discord sticker id must be a decimal snowflake');
  }
  return value;
}

function normalizeFormatType(formatType) {
  const value = Number(formatType);
  if (!Number.isInteger(value) || ![1, 2, 3, 4].includes(value)) {
    throw new StickerMediaError('UNSUPPORTED_STICKER_FORMAT', 'Unsupported Discord sticker format_type');
  }
  return value;
}

export function resolveDiscordStickerMedia({ nativeId, formatType }) {
  const stickerId = assertStickerId(nativeId);
  const format = normalizeFormatType(formatType);

  if (format === 4) {
    return Object.freeze({
      stickerId,
      formatType: format,
      url: `https://${DISCORD_MEDIA_HOST}/stickers/${stickerId}.gif`,
      expectedKind: 'gif',
      expectedMime: 'image/gif',
    });
  }

  if (format === 3) {
    return Object.freeze({
      stickerId,
      formatType: format,
      url: `https://${DISCORD_CDN_HOST}/stickers/${stickerId}.json`,
      expectedKind: 'lottie',
      expectedMime: 'application/json',
    });
  }

  return Object.freeze({
    stickerId,
    formatType: format,
    url: `https://${DISCORD_CDN_HOST}/stickers/${stickerId}.png`,
    expectedKind: format === 2 ? 'apng' : 'png',
    expectedMime: 'image/png',
  });
}

function startsWith(bytes, signature) {
  if (bytes.length < signature.length) return false;
  for (let index = 0; index < signature.length; index += 1) {
    if (bytes[index] !== signature[index]) return false;
  }
  return true;
}

function asciiIncludes(bytes, text) {
  const needle = new TextEncoder().encode(text);
  outer: for (let start = 0; start <= bytes.length - needle.length; start += 1) {
    for (let offset = 0; offset < needle.length; offset += 1) {
      if (bytes[start + offset] !== needle[offset]) continue outer;
    }
    return true;
  }
  return false;
}

function normalizeContentType(headers) {
  return String(headers?.get?.('content-type') ?? '')
    .split(';', 1)[0]
    .trim()
    .toLowerCase();
}

function validatePng(bytes, { animated }) {
  if (!startsWith(bytes, PNG_SIGNATURE)) {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'Discord sticker payload is not a PNG/APNG stream');
  }
  if (animated && !asciiIncludes(bytes, 'acTL')) {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'APNG sticker is missing the animation control chunk');
  }
}

function validateGif(bytes) {
  if (!startsWith(bytes, GIF87A) && !startsWith(bytes, GIF89A)) {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'Discord sticker payload is not a GIF stream');
  }
}

function validateLottie(bytes) {
  let parsed;
  try {
    parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'Discord Lottie sticker is not valid UTF-8 JSON');
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'Discord Lottie sticker must be a JSON object');
  }
  const width = Number(parsed.w);
  const height = Number(parsed.h);
  const frameRate = Number(parsed.fr);
  const inPoint = Number(parsed.ip);
  const outPoint = Number(parsed.op);
  if (
    !Array.isArray(parsed.layers)
    || !Number.isFinite(width) || width <= 0 || width > 4096
    || !Number.isFinite(height) || height <= 0 || height > 4096
    || !Number.isFinite(frameRate) || frameRate <= 0 || frameRate > 240
    || !Number.isFinite(inPoint)
    || !Number.isFinite(outPoint) || outPoint <= inPoint
  ) {
    throw new StickerMediaError('INVALID_STICKER_CONTENT', 'Discord Lottie sticker failed structural validation');
  }
}

function validateStickerBytes(bytes, resolved, contentType) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0) {
    throw new StickerMediaError('EMPTY_STICKER_CONTENT', 'Discord sticker response was empty');
  }

  if (resolved.expectedKind === 'png') {
    if (contentType && contentType !== 'image/png' && contentType !== 'application/octet-stream') {
      throw new StickerMediaError('UNEXPECTED_STICKER_MIME', `Unexpected Discord sticker MIME: ${contentType}`);
    }
    validatePng(bytes, { animated: false });
  } else if (resolved.expectedKind === 'apng') {
    if (contentType && !['image/png', 'image/apng', 'application/octet-stream'].includes(contentType)) {
      throw new StickerMediaError('UNEXPECTED_STICKER_MIME', `Unexpected Discord sticker MIME: ${contentType}`);
    }
    validatePng(bytes, { animated: true });
  } else if (resolved.expectedKind === 'gif') {
    if (contentType && contentType !== 'image/gif' && contentType !== 'application/octet-stream') {
      throw new StickerMediaError('UNEXPECTED_STICKER_MIME', `Unexpected Discord sticker MIME: ${contentType}`);
    }
    validateGif(bytes);
  } else {
    if (contentType && !['application/json', 'text/json', 'application/octet-stream'].includes(contentType)) {
      throw new StickerMediaError('UNEXPECTED_STICKER_MIME', `Unexpected Discord sticker MIME: ${contentType}`);
    }
    validateLottie(bytes);
  }
}

async function readBoundedBody(response, maxBytes) {
  const rawLength = response.headers?.get?.('content-length');
  if (rawLength) {
    const declared = Number(rawLength);
    if (Number.isFinite(declared) && declared > maxBytes) {
      throw new StickerMediaError('STICKER_TOO_LARGE', 'Discord sticker exceeds the configured byte limit');
    }
  }

  if (!response.body?.getReader) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > maxBytes) {
      throw new StickerMediaError('STICKER_TOO_LARGE', 'Discord sticker exceeds the configured byte limit');
    }
    return bytes;
  }

  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) {
        throw new StickerMediaError('INVALID_STICKER_STREAM', 'Discord sticker stream returned a non-byte chunk');
      }
      size += value.byteLength;
      if (size > maxBytes) {
        try { await reader.cancel(); } catch { /* best-effort */ }
        throw new StickerMediaError('STICKER_TOO_LARGE', 'Discord sticker exceeds the configured byte limit');
      }
      chunks.push(value);
    }
  } finally {
    try { reader.releaseLock?.(); } catch { /* best-effort */ }
  }

  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export async function fetchDiscordStickerMedia(
  reference,
  {
    fetchImpl = globalThis.fetch,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    maxBytes = DEFAULT_MAX_BYTES,
  } = {},
) {
  const resolved = resolveDiscordStickerMedia(reference);
  if (typeof fetchImpl !== 'function') {
    throw new StickerMediaError('FETCH_UNAVAILABLE', 'No fetch implementation is available');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 30_000) {
    throw new StickerMediaError('INVALID_FETCH_TIMEOUT', 'Sticker fetch timeout is outside the allowed range');
  }
  if (!Number.isInteger(maxBytes) || maxBytes < 1024 || maxBytes > 4 * 1024 * 1024) {
    throw new StickerMediaError('INVALID_FETCH_LIMIT', 'Sticker fetch byte limit is outside the allowed range');
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(resolved.url, {
      method: 'GET',
      redirect: 'error',
      signal: controller.signal,
      headers: {
        accept: resolved.expectedMime,
        'user-agent': 'Amora-Chibi/visual-expression-enrichment',
      },
    });
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new StickerMediaError('STICKER_FETCH_TIMEOUT', 'Discord sticker fetch timed out');
    }
    if (error instanceof StickerMediaError) throw error;
    throw new StickerMediaError('STICKER_FETCH_FAILED', 'Discord sticker fetch failed');
  } finally {
    clearTimeout(timer);
  }

  if (!response?.ok || response.status !== 200) {
    throw new StickerMediaError('STICKER_FETCH_HTTP_ERROR', `Discord sticker fetch returned HTTP ${response?.status ?? 'unknown'}`);
  }
  if (response.url) {
    const finalUrl = new URL(response.url);
    const expectedUrl = new URL(resolved.url);
    if (finalUrl.protocol !== 'https:' || finalUrl.hostname !== expectedUrl.hostname || finalUrl.pathname !== expectedUrl.pathname) {
      throw new StickerMediaError('STICKER_FETCH_REDIRECTED', 'Discord sticker response escaped the derived CDN endpoint');
    }
  }

  const bytes = await readBoundedBody(response, maxBytes);
  const contentType = normalizeContentType(response.headers);
  validateStickerBytes(bytes, resolved, contentType);

  return Object.freeze({
    bytes,
    mime: resolved.expectedMime,
    formatType: resolved.formatType,
    stickerId: resolved.stickerId,
    sourceHost: new URL(resolved.url).hostname,
  });
}
