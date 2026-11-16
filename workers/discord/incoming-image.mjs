/** One-shot inbound Discord image for an already accepted DM.
 * Only the native Discord CDN is accessed. No arbitrary URL, redirects, disk
 * cache, attachment metadata as instructions, or unbounded response bodies.
 */
const ALLOWED_HOSTS = new Set(['cdn.discordapp.com', 'media.discordapp.net']);
const MIMES = new Set(['image/png', 'image/jpeg', 'image/webp']);
export const MAX_INBOUND_IMAGE_BYTES = 4 * 1024 * 1024;
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

function safeMediaUrl(attachment, message) {
  const raw = attachment?.proxy_url || attachment?.url;
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

async function boundedImageResponse(response) {
  if (!response?.ok || !response.body) throw new IncomingImageError('IMAGE_CDN_UNAVAILABLE');
  const announced = Number(response.headers?.get?.('content-length'));
  if (Number.isFinite(announced) && announced > MAX_INBOUND_IMAGE_BYTES) {
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
      if (size > MAX_INBOUND_IMAGE_BYTES) throw new IncomingImageError('IMAGE_TOO_LARGE');
      chunks.push(Buffer.from(value));
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (!size) throw new IncomingImageError('IMAGE_EMPTY');
  return Buffer.concat(chunks, size);
}

export async function collectDiscordIncomingImage(message, {
  fetchImpl = fetch, timeoutMs = MAX_TIMEOUT_MS,
} = {}) {
  if (!message?.id || !snowflake(message?.channel_id) || message.guild_id) return null;
  const candidate = (Array.isArray(message.attachments) ? message.attachments : [])
    .find((item) => MIMES.has(String(item?.content_type || '').split(';')[0].toLowerCase()));
  if (!candidate) return null;
  if (!snowflake(candidate.id)) throw new IncomingImageError('IMAGE_ATTACHMENT_ID_INVALID');
  if (Number.isFinite(candidate.size) && candidate.size > 25 * 1024 * 1024) {
    // Don't fetch even the proxy for grossly oversized native uploads.
    throw new IncomingImageError('IMAGE_NATIVE_TOO_LARGE');
  }
  const url = safeMediaUrl(candidate, message);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.min(MAX_TIMEOUT_MS, timeoutMs));
  try {
    const response = await fetchImpl(url, { method: 'GET', redirect: 'error', signal: controller.signal });
    const bytes = await boundedImageResponse(response);
    const mime = mimeFromSignature(bytes);
    const declared = String(candidate.content_type).split(';')[0].toLowerCase();
    if (mime !== declared) throw new IncomingImageError('IMAGE_MIME_MISMATCH');
    return {
      attachment_id: String(candidate.id),
      content_mime: mime,
      content_base64: bytes.toString('base64'),
    };
  } catch (error) {
    if (error instanceof IncomingImageError) throw error;
    throw new IncomingImageError(error?.name === 'AbortError' ? 'IMAGE_FETCH_TIMEOUT' : 'IMAGE_FETCH_FAILED');
  } finally {
    clearTimeout(timer);
  }
}
