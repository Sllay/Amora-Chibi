/** One-shot, fidelity-preserving inbound Discord art for an accepted DM.
 * Only the native Discord CDN is accessed. No arbitrary URL, redirects, disk
 * cache, attachment metadata as instructions, or unbounded response bodies.
 */
import { transcodeOversizedImage, ImageTransformError } from './incoming-image-transform.mjs';

const ALLOWED_HOSTS = new Set(['cdn.discordapp.com', 'media.discordapp.net']);
const CORE_MIMES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const NATIVE_MIMES = new Set([
  ...CORE_MIMES, 'image/heic', 'image/heif', 'image/avif',
]);
const EXTENSION_MIMES = Object.freeze({
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  jfif: 'image/jpeg', webp: 'image/webp',
  heic: 'image/heic', heif: 'image/heif', avif: 'image/avif',
});

function nativeMimeHint(item) {
  const declared = String(item?.content_type || '').split(';')[0].toLowerCase().trim();
  if (NATIVE_MIMES.has(declared)) return declared;
  // Discord's content_type is optional. Use the filename only as a
  // candidate hint, then verify the actual magic signature after download.
  if (declared && declared !== 'application/octet-stream') return null;
  const ext = /\.([a-z0-9]+)$/i.exec(String(item?.filename || ''))?.[1]?.toLowerCase();
  return EXTENSION_MIMES[ext] || null;
}
// Keep the Core->worker wire payload small even for large native artwork.
export const MAX_INBOUND_IMAGE_BYTES = 2_800_000;
export const MAX_GROQ_URL_IMAGE_BYTES = 20_000_000;
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
  if (bytes.length >= 16 && bytes.subarray(4, 8).toString('ascii') === 'ftyp') {
    const length = bytes.readUInt32BE(0);
    if (length >= 16 && length <= Math.min(bytes.length, 256)
        && length % 4 === 0) {
      const brands = [];
      for (let i = 8; i + 4 <= length; i += 4) {
        if (i === 12) continue; // minor version
        brands.push(bytes.subarray(i, i + 4).toString('ascii'));
      }
      if (brands.includes('avif') || brands.includes('avis')) return 'image/avif';
      if (brands.some((brand) =>
        ['heic', 'heix', 'hevc', 'hevx', 'heim', 'heis'].includes(brand)
      )) return 'image/heic';
    }
  }
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
  onStage = () => {},
} = {}) {
  if (!message?.id || !snowflake(message?.channel_id) || message.guild_id) return null;
  const candidate = (Array.isArray(message.attachments) ? message.attachments.slice(0, 16) : [])
    .find((item) => nativeMimeHint(item) !== null);
  if (!candidate) return null;
  if (!snowflake(candidate.id)) throw new IncomingImageError('IMAGE_ATTACHMENT_ID_INVALID');
  onStage('candidate');
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
      onStage(field === 'url' ? 'original_fetch' : 'proxy_fetch');
      const response = await fetchImpl(url, {
        method: 'GET', redirect: 'error', signal: controller.signal,
      });
      const source = await boundedImageResponse(response);
      onStage('downloaded');
      const mime = mimeFromSignature(source);
      const declared = nativeMimeHint(candidate);
      const nativeMatches = mime === declared ||
        (declared === 'image/heif' && mime === 'image/heic');
      if (field === 'url' && !nativeMatches) {
        throw new IncomingImageError('IMAGE_MIME_MISMATCH');
      }
      let bytes = source;
      let outputMime = mime;
      // Groq advertises a 20 MB limit for *image URL* input, while inline
      // Base64 can have a smaller request cap. An original from the verified
      // Discord CDN is eligible for direct visual reading only if its bytes
      // and native MIME passed all the checks above. The signed URL never
      // enters conversation memory, logs or a generic URL-fetch tool.
      const sourceUrl = (
        field === 'url' && CORE_MIMES.has(mime)
        && source.length > MAX_INBOUND_IMAGE_BYTES
        && source.length <= MAX_GROQ_URL_IMAGE_BYTES
      ) ? url : null;
      // HEIC and AVIF are decoded locally into the closed, provider-safe
      // image MIME contract even when the native file is under 4 MiB.
      if (source.length > MAX_INBOUND_IMAGE_BYTES || !CORE_MIMES.has(mime)) {
        onStage('transcode_started');
        const converted = await convertImpl(source, mime);
        if (!converted || !Buffer.isBuffer(converted.bytes) ||
            !converted.bytes.length || converted.bytes.length > MAX_INBOUND_IMAGE_BYTES ||
            converted.mime !== mimeFromSignature(converted.bytes)) {
          throw new IncomingImageError('IMAGE_TRANSFORM_OUTPUT_INVALID');
        }
        bytes = converted.bytes;
        outputMime = converted.mime;
        onStage('transcode_completed');
      }
      onStage('ready');
      return {
        attachment_id: String(candidate.id),
        content_mime: outputMime,
        content_base64: bytes.toString('base64'),
        ...(sourceUrl ? { source_url: sourceUrl } : {}),
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
