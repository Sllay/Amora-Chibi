// Optional external screenshot capture for explicit owner-DM web requests.
// No Chromium process in Discord. Explicit backend selection; the optional
// authenticated Browser Worker never falls through to a third-party provider.
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
const exec = promisify(execFile);
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

// Verify format, dimensions and an actual frame decode using the existing
// ffmpeg package in the non-root Discord image. No shell or network protocol.
export async function verifyScreenshotRaster(bytes, mime) {
  if (!(bytes instanceof Uint8Array) || !bytes.length || bytes.length > MAX_IMAGE_BYTES
      || !looksLikeImage(bytes, mime)) return null;
  const suffix = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }[mime];
  const codec = { 'image/jpeg': 'mjpeg', 'image/png': 'png', 'image/webp': 'webp' }[mime];
  if (!suffix) return null;
  let dir;
  try {
    dir = await mkdtemp(join(tmpdir(), 'amora-web-raster-'));
    const path = join(dir, 'capture.' + suffix);
    await writeFile(path, bytes, { mode: 0o600, flag: 'wx' });
    const opts = { timeout: 3500, maxBuffer: 16384, windowsHide: true,
      env: { PATH: process.env.PATH || '/usr/bin:/bin', HOME: tmpdir() } };
    const input = ['-protocol_whitelist', 'file,pipe', '-f', 'image2', '-pattern_type', 'none', '-i', path];
    const probe = await exec('ffprobe', ['-v', 'error', ...input, '-show_entries',
      'stream=codec_name,width,height', '-of', 'json'], opts);
    const streams = JSON.parse(probe.stdout).streams;
    const row = streams?.[0];
    if (streams?.length !== 1 || row?.codec_name !== codec ||
        !Number.isInteger(row.width) || !Number.isInteger(row.height) ||
        row.width < 1 || row.height < 1 || row.width > 4096 || row.height > 4096 ||
        row.width * row.height > 8_000_000) return null;
    await exec('ffmpeg', ['-v', 'error', '-xerror', '-err_detect', 'explode',
      '-max_alloc', '67108864', '-threads', '1', ...input,
      '-frames:v', '1', '-f', 'null', '-'], opts);
    return { width: row.width, height: row.height,
      checksum: createHash('sha256').update(bytes).digest('hex') };
  } catch {
    return null;
  } finally {
    if (dir) await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}

function browserEndpoint(value, token, privateHttp) {
  try {
    const u = new URL(value);
    if (!token || token.length < 32 || u.username || u.password || u.search || u.hash ||
        u.pathname !== '/' || (u.protocol !== 'https:' && !(privateHttp && u.protocol === 'http:'))) return null;
    return new URL('/capture', u).toString();
  } catch { return null; }
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
  constructor({ fetchImpl = fetch, enabled = true, apiKey = '', dailyLimit = DEFAULT_DAILY_LIMIT,
    backend = 'microlink', browserUrl = '', browserToken = '', privateHttp = false,
    verifyRaster = verifyScreenshotRaster } = {}) {
    this.fetchImpl = fetchImpl;
    this.enabled = enabled;
    this.backend = backend;
    this.browserToken = browserToken;
    this.browserEndpoint = browserEndpoint(browserUrl, browserToken, privateHttp);
    this.verifyRaster = verifyRaster;
    this.apiKey = String(apiKey || '').trim();
    this.dailyLimit = Math.max(0, Math.min(10, Number(dailyLimit) || 0));
    this.day = '';
    this.used = 0;
    this.lastFailureCode = null;
    // At most two public screenshots (typically <5MB total), five-minute TTL.
    // Repeated Discord retry for the same site does not consume Microlink quota.
    this.recent = new Map();
  }

  _failed(code) {
    this.lastFailureCode = code;
    return null;
  }

  async capture(value, { captureId = '', trace = () => {} } = {}) {
    this.lastFailureCode = null;
    const target = publicScreenshotTarget(value);
    if (!this.enabled) return this._failed('disabled');
    if (!target) return this._failed('invalid_public_url');
    if (!['microlink', 'browser'].includes(this.backend) ||
        (this.backend === 'browser' && !this.browserEndpoint)) return this._failed('backend_unavailable');
    const cached = this.recent.get(target);
    if (cached && Date.now() - cached.when < 300_000) {
      trace('cache_hit', { bytes: cached.image.bytes.length, ...cached.image.raster });
      return cached.image;
    }
    if (cached) this.recent.delete(target);
    const today = new Date().toISOString().slice(0, 10);
    if (today !== this.day) { this.day = today; this.used = 0; }
    if (this.used >= this.dailyLimit) return this._failed('daily_limit');
    this.used += 1; // reserve before async network; no parallel quota escape
    const started = Date.now();
    if (this.backend === 'browser') {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 18000);
      try {
        const response = await this.fetchImpl(this.browserEndpoint, {
          method: 'POST', redirect: 'error', signal: controller.signal,
          headers: { 'Content-Type': 'application/json', 'X-Amora-Browser-Token': this.browserToken,
            ...(/^[a-f0-9]{24}$/.test(captureId) ? { 'X-Amora-Capture-Id': captureId } : {}) },
          body: JSON.stringify({ url: target }),
        });
        trace('browser_response', { status: response.status, elapsed_ms: Date.now() - started });
        if (!response.ok) return this._failed(response.status === 422 ? 'navigation_failed' :
          response.status === 504 ? 'provider_timeout' : 'backend_unavailable');
        const type = String(response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
        if (type !== 'image/png') return this._failed('image_mime_invalid');
        const data = await boundedResponse(response, MAX_IMAGE_BYTES);
        const raster = data && await this.verifyRaster(data, type);
        if (!raster) return this._failed('image_bytes_invalid');
        const screenshot = { bytes: data, mime: type, filename: 'amora-web-print.png', raster };
        trace('raster_validated', { bytes: data.length, mime: type, ...raster, elapsed_ms: Date.now() - started });
        this.recent.set(target, { when: Date.now(), image: screenshot });
        while (this.recent.size > 2) this.recent.delete(this.recent.keys().next().value);
        return screenshot;
      } catch {
        return this._failed(controller.signal.aborted ? 'provider_timeout' : 'provider_network_error');
      } finally { clearTimeout(timer); }
    }
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
      trace('render_response', { status: answer.status, elapsed_ms: Date.now() - started });
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
      trace('download_response', { status: image.status, elapsed_ms: Date.now() - started });
      if (!image.ok) return this._failed('image_cdn_http_error');
      const type = String(image.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(type)) return this._failed('image_mime_invalid');
      const data = await boundedResponse(image, MAX_IMAGE_BYTES);
      const raster = data && await this.verifyRaster(data, type);
      if (!raster) return this._failed('image_bytes_invalid');
      trace('raster_validated', { bytes: data.length, mime: type, ...raster, elapsed_ms: Date.now() - started });
      const screenshot = {
        bytes: data, raster,
        // Already verified against the small Microlink-owned CDN allowlist.
        imageUrl: screenshotUrl,
        mime: type,
        filename: type === 'image/png' ? 'amora-web-print.png'
          : type === 'image/webp' ? 'amora-web-print.webp' : 'amora-web-print.jpg',
      };
      this.recent.set(target, { when: Date.now(), image: screenshot });
      while (this.recent.size > 2) this.recent.delete(this.recent.keys().next().value);
      return screenshot;
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
