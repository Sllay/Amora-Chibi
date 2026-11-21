// Optional external screenshot capture for explicit owner-DM web requests.
// No Chromium/Playwright process, no binary decode dependency. Never read the
// target website locally: only the fixed Microlink API receives the public URL.
// Rate-limited, content-typed, bounded, and always fails closed to text links.
const MAX_IMAGE_BYTES = 2_500_000;
const MAX_JSON_BYTES = 32_000;
const DEFAULT_DAILY_LIMIT = 4;
const API = 'https://api.microlink.io/';
const PRO_API = 'https://pro.microlink.io/';

const sensitiveKeys = new Set([
  'token', 'access_token', 'apikey', 'api_key', 'password',
  'passwd', 'key', 'auth', 'authorization', 'secret', 'signature',
  'sig', 'jwt', 'session', 'sessionid', 'code',
]);

export function publicScreenshotTarget(value) {
  if (typeof value !== 'string' || !value || value.length > 2048 || /[\x00-\x1f\x7f]/.test(value)) return null;
  try {
    const url = new URL(value);
    const name = url.hostname.toLowerCase();
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) return null;
    if (!name || name === 'localhost' || name.endsWith('.') || /(?:\.local|\.internal|\.localhost|\.onion)$/.test(name)) return null;
    if (/^[\d.]+$/.test(name) || name.includes(':')) return null; // IP literal: no screenshot vendor SSRF.
    if (url.port && !['80', '443', '8080', '8443'].includes(url.port)) return null;
    for (const key of url.searchParams.keys()) if (sensitiveKeys.has(key.toLowerCase())) return null;
    return url.toString();
  } catch {
    return null;
  }
}

function publicScreenshotCdn(value) {
  try {
    const u = new URL(value);
    const host = u.hostname.toLowerCase();
    // The official Microlink API also uses this specific S3 bucket for
    // screenshots. Never allow generic S3 endpoints or arbitrary buckets.
    const trustedAssetHost = /^(?:cdn|[a-z]{3}|[a-z]{3}\d)\.microlink\.io$/.test(host)
      || host === 'microlink-cdn.s3.amazonaws.com';
    return (
      u.protocol === 'https:' && !u.username && !u.password
      && !u.port && trustedAssetHost && u.toString().length <= 2500
    ) ? u.toString() : null;
  } catch {
    return null;
  }
}

function looksLikeImage(bytes, mime) {
  const p = bytes;
  if (mime === 'image/jpeg') return p.length > 4 && p[0] === 0xff && p[1] === 0xd8 && p[2] === 0xff;
  if (mime === 'image/png') return p.length > 8 && p[0] === 0x89 && p[1] === 0x50 && p[2] === 0x4e && p[3] === 0x47;
  if (mime === 'image/webp') return p.length > 12 && String.fromCharCode(...p.slice(0, 4)) === 'RIFF'
    && String.fromCharCode(...p.slice(8, 12)) === 'WEBP';
  return false;
}

async function boundedResponse(response, maxBytes) {
  const size = Number(response.headers.get('content-length') || 0);
  if (size > maxBytes || !response.body) return null;
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) return null;
      chunks.push(value);
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const combined = new Uint8Array(length);
  let offset = 0;
  for (const part of chunks) { combined.set(part, offset); offset += part.byteLength; }
  return combined;
}

export class WebScreenshotService {
  constructor({ fetchImpl = fetch, enabled = true, apiKey = '', dailyLimit = DEFAULT_DAILY_LIMIT } = {}) {
    this.fetchImpl = fetchImpl;
    this.enabled = enabled;
    this.apiKey = String(apiKey || '').trim();
    this.dailyLimit = Math.max(0, Math.min(10, Number(dailyLimit) || 0));
    this.day = '';
    this.used = 0;
    this.lastFailureCode = null;
  }

  _failed(code) {
    this.lastFailureCode = code;
    return null;
  }

  async capture(value) {
    this.lastFailureCode = null;
    const target = publicScreenshotTarget(value);
    if (!this.enabled) return this._failed('disabled');
    if (!target) return this._failed('invalid_public_url');
    const today = new Date().toISOString().slice(0, 10);
    if (today !== this.day) { this.day = today; this.used = 0; }
    if (this.used >= this.dailyLimit) return this._failed('daily_limit');
    this.used += 1; // reserve before async network; no parallel quota escape
    const endpoint = new URL(this.apiKey ? PRO_API : API);
    endpoint.searchParams.set('url', target);
    endpoint.searchParams.set('screenshot', 'true');
    endpoint.searchParams.set('screenshot.type', 'jpeg');
    endpoint.searchParams.set('screenshot.quality', '65');
    endpoint.searchParams.set('meta', 'false');

    const controller = new AbortController();
    // Remote pages can spend >9 s rendering. This does not allocate a
    // Chromium process on the Core and remains below the outbound claim lease.
    const timer = setTimeout(() => controller.abort(), 18000);
    try {
      const answer = await this.fetchImpl(endpoint.toString(), {
        method: 'GET',
        redirect: 'error', signal: controller.signal,
        headers: this.apiKey ? { 'x-api-key': this.apiKey } : {},
      });
      if (!answer.ok) return this._failed(
        answer.status === 429 ? 'provider_rate_limited'
          : answer.status === 401 || answer.status === 403 ? 'provider_auth_required'
            : 'provider_http_error'
      );
      const raw = await boundedResponse(answer, MAX_JSON_BYTES);
      if (!raw) return this._failed('provider_response_invalid');
      const record = JSON.parse(new TextDecoder().decode(raw));
      if (record.status !== 'success') return this._failed('provider_refused');
      const screenshotUrl = publicScreenshotCdn(record.data?.screenshot?.url);
      if (!screenshotUrl) return this._failed('provider_image_url_invalid');
      const image = await this.fetchImpl(screenshotUrl, {
        method: 'GET', redirect: 'error', signal: controller.signal,
      });
      if (!image.ok) return this._failed('image_cdn_http_error');
      const type = String(image.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(type)) return this._failed('image_mime_invalid');
      const data = await boundedResponse(image, MAX_IMAGE_BYTES);
      if (!data || !looksLikeImage(data, type)) return this._failed('image_bytes_invalid');
      return {
        bytes: data,
        // Already verified against the small Microlink-owned CDN allowlist.
        // Used only as a visible fallback if Discord rejects the file upload.
        imageUrl: screenshotUrl,
        mime: type,
        filename: type === 'image/png' ? 'amora-web-print.png'
          : type === 'image/webp' ? 'amora-web-print.webp' : 'amora-web-print.jpg',
      };
    } catch {
      return this._failed(controller.signal.aborted ? 'provider_timeout' : 'provider_network_error');
    } finally {
      clearTimeout(timer);
    }
  }
}

export function screenshotMultipart(payload, screenshot) {
  const form = new FormData();
  form.append('payload_json', JSON.stringify({
    ...payload,
    attachments: [{ id: 0, filename: screenshot.filename, description: 'Captura da página web' }],
  }));
  form.append('files[0]', new Blob([screenshot.bytes], { type: screenshot.mime }), screenshot.filename);
  return form;
}
