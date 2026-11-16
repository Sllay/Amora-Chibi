/** One-shot, fidelity-preserving inbound Discord art for an accepted DM.
 * Only the native Discord CDN is accessed. No arbitrary URL, redirects, disk
 * cache, attachment metadata as instructions, or unbounded response bodies.
 */
import { transcodeOversizedImage, ImageTransformError } from './incoming-image-transform.mjs';

const ALLOWED_HOSTS = new Set(['cdn.discordapp.com', 'media.discordapp.net']);
const MIMES = new Set(['image/png', 'image/jpeg', 'image/webp']);
// Keep the Core->worker wire payload small even for large native artwork.
export const MAX_INBOUND_IMAGE_BYTES = 4 * 1024 * 1024;
export const MAX_NATIVE_IMAGE_BYTES = 25 * 1024 * 1024;
const MAX_TIMEOUT_MS = 8_000;

export class IncomingImageError extends Error {
  constructor(code) {
    super(code);
    this.name = 'IncomingImageError';
    this.code = code;
  }
}

function snowflake(value) {
  return /^[0-9]{15,22}$/.test(String(value ?? ''));
}

function safeMediaUrl(attachment, message, field = 'url') {
  const raw = attachment?.[field];
  if (typeof raw !== 'string' || raw.length > 2048) throw new IncomingImageError('IMAGE_URL_INVALID');
  let url;
  try { url = new URL(raw); } catch { throw new IncomingImageError('IMAGE_URL_INVALID'); }
  if (url.protocol !== 'https:' || !ALLOWED_HOSTS.has(url.hostname)
      || url.username || url.password || url.port || url.hash) {
    throw new IncomingImageError('IMAGE_URL_NOT_DISCORD_CDN');
  }
  const pieces = url.pathname.split('/');
  // /attachments/<channel snowflake>/<attachment snowflake>/<filename>
  if (pieces.length < 5 || pieces[1] !== 'attachments'
      || !snowflake(pieces[2]) || pieces[2] !== String(message.channel_id)
      || !snowflake(pieces[3]) || pieces[3] !== String(attachment.id)
      || !pieces.slice(4).every((part) => part && part !== '..' && part !== '.')) {
    throw new IncomingImageError('IMAGE_ATTACHMENT_ID_MISMATCH');
  }
  return url.toString();
}

function mimeFromSignature(bytes) {
  if (bytes.length >= 8 && bytes[0] === 0x89 && bytes[1] === 0x50
      && bytes[2] === 0x4e && bytes[3] === 0x47 && bytes[4] === 13
      && bytes[5] === 10 && bytes[6] === 26 && bytes[7] === 10) return 'image/png';
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8
      && bytes[2] === 0xff) return 'image/jpeg';
  if (bytes.length >= 12 && Buffer.from(bytes.subarray(0, 4)).toString('ascii') === 'RIFF'
      && Buffer.from(bytes.subarray(8, 12)).toString('ascii') === 'WEBP') return 'image/webp';
  throw new IncomingImageError('IMAGE_SIGNATURE_INVALID');
}

async function boundedImageResponse(response, maxBytes = MAX_NATIVE_IMAGE_BYTES) {
  if (!response?.ok || !response.body) throw new IncomingImageError('IMAGE_CDN_UNAVAILABLE');
  const announced = Number(response.headers?.get?.('content-length'));
  if (Number.isFinite(announced) && announced > maxBytes) {
    await response.body.cancel?.();
    throw new IncomingImageError('IMAGE_TOO_LARGE');
  }
  const chunks = [];
  let size = 0;
  const reader = response.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value?.byteLength || 0;
      if (size > maxBytes) throw new IncomingImageError('IMAGE_TOO_LARGE');
      chunks.push(Buffer.from(value));
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (!size) throw new IncomingImageError('IMAGE_EMPTY');
  return Buffer.concat(chunks, size);
}

/**
 * Preserve the native PNG/JPEG/WebP when it fits in the 4 MiB Core envelope.
 * For larger files, fetch up to 25 MiB and make a quality-preserving WebP
 * preview locally. This avoids lossy implicit Discord proxy thumbnails.
 */
export async function collectDiscordIncomingImage(message, {
  fetchImpl = fetch,
  timeoutMs = MAX_TIMEOUT_MS,
  convertImpl = transcodeOversizedImage,
} = {}) {
  if (!message?.id || !snowflake(message?.channel_id) || message.guild_id) return null;
  const candidate = (Array.isArray(message.attachments) ? message.attachments.slice(0, 16) : [])
    .find((item) => MIMES.has(String(item?.content_type || '').split(';')[0].toLowerCase()));
  if (!candidate) return null;
  if (!snowflake(candidate.id)) throw new IncomingImageError('IMAGE_ATTACHMENT_ID_INVALID');
  if (Number.isFinite(candidate.size) && candidate.size > MAX_NATIVE_IMAGE_BYTES) {
    throw new IncomingImageError('IMAGE_NATIVE_TOO_LARGE');
  }

  // Prefer original, not Discord proxy: proxy may transcode image/png to
  // image/webp, so native metadata MIME must not be used for its bytes.
  const sourceFields = ['url', 'proxy_url'];
  let lastFetchError = null;
  for (const field of sourceFields) {
    if (typeof candidate[field] !== 'string' || !candidate[field]) continue;
    let url;
    try {
      url = safeMediaUrl(candidate, message, field);
    } catch (error) {
      // A malformed/unsafe native URL never creates a new external fetch.
      // A separately validated Discord proxy URL may still be available.
      lastFetchError = error;
      continue;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.min(MAX_TIMEOUT_MS, timeoutMs));
    try {
      const response = await fetchImpl(url, {
        method: 'GET', redirect: 'error', signal: controller.signal,
      });
      const source = await boundedImageResponse(response);
      const mime = mimeFromSignature(source);
      const declared = String(candidate.content_type).split(';')[0].toLowerCase();
      if (field === 'url' && mime !== declared) {
        throw new IncomingImageError('IMAGE_MIME_MISMATCH');
      }
      let bytes = source;
      let outputMime = mime;
      if (source.length > MAX_INBOUND_IMAGE_BYTES) {
        const converted = await convertImpl(source, mime);
        if (!converted || !Buffer.isBuffer(converted.bytes) ||
            !converted.bytes.length || converted.bytes.length > MAX_INBOUND_IMAGE_BYTES ||
            converted.mime !== mimeFromSignature(converted.bytes)) {
          throw new IncomingImageError('IMAGE_TRANSFORM_OUTPUT_INVALID');
        }
        bytes = converted.bytes;
        outputMime = converted.mime;
      }
      return {
        attachment_id: String(candidate.id),
        content_mime: outputMime,
        content_base64: bytes.toString('base64'),
      };
    } catch (error) {
      if (error instanceof ImageTransformError) throw new IncomingImageError(error.code);
      if (error instanceof IncomingImageError) {
        if (!['IMAGE_CDN_UNAVAILABLE', 'IMAGE_TOO_LARGE'].includes(error.code)) throw error;
        lastFetchError = error;
      } else {
        lastFetchError = new IncomingImageError(
          error?.name === 'AbortError' ? 'IMAGE_FETCH_TIMEOUT' : 'IMAGE_FETCH_FAILED',
        );
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastFetchError || new IncomingImageError('IMAGE_URL_INVALID');
}
