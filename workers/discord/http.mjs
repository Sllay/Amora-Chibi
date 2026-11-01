const API = 'https://discord.com/api/v10';
const sleepDefault = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function parseRetryMs(response, payload) {
  const bodySeconds = Number(payload?.retry_after);
  if (Number.isFinite(bodySeconds) && bodySeconds >= 0) return bodySeconds * 1000;
  const retryAfter = Number(response.headers.get('retry-after'));
  if (Number.isFinite(retryAfter) && retryAfter >= 0) return retryAfter * 1000;
  const resetAfter = Number(response.headers.get('x-ratelimit-reset-after'));
  if (Number.isFinite(resetAfter) && resetAfter >= 0) return resetAfter * 1000;
  return null;
}

export class DiscordHttpError extends Error {
  constructor(message, { status = null, code = null, retryable = false } = {}) {
    super(message);
    this.name = 'DiscordHttpError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

/**
 * Small REST client with bounded retry semantics.
 *
 * 429 obeys Discord-provided retry timing; 5xx/network failures receive bounded
 * exponential retry. 401/403/404 are terminal so permission/config problems do
 * not become a hot retry loop.
 */
export class DiscordRestClient {
  constructor({ token, fetchImpl = fetch, sleep = sleepDefault, random = Math.random, maxAttempts = 4 } = {}) {
    if (!token) throw new Error('DISCORD_BOT_TOKEN is required');
    this.token = token;
    this.fetchImpl = fetchImpl;
    this.sleep = sleep;
    this.random = random;
    this.maxAttempts = Math.max(1, Math.min(6, Number(maxAttempts) || 4));
  }

  async request(path, { method = 'GET', body, timeoutMs = 15_000 } = {}) {
    let lastError = null;
    for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await this.fetchImpl(`${API}${path}`, {
          method,
          redirect: 'error',
          headers: {
            Authorization: `Bot ${this.token}`,
            ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
        const text = await response.text();
        let payload = null;
        if (text) {
          try { payload = JSON.parse(text); } catch {}
        }

        if (response.ok) return payload;

        if (response.status === 429) {
          const base = parseRetryMs(response, payload) ?? 1000;
          const waitMs = Math.min(30_000, Math.max(250, base + Math.floor(this.random() * 250)));
          lastError = new DiscordHttpError('Discord rate limited', {
            status: 429,
            code: 'RATE_LIMITED',
            retryable: true,
          });
          if (attempt < this.maxAttempts) {
            await this.sleep(waitMs);
            continue;
          }
          throw lastError;
        }

        if (response.status >= 500 && response.status <= 599) {
          lastError = new DiscordHttpError(`Discord HTTP ${response.status}`, {
            status: response.status,
            code: 'UPSTREAM_5XX',
            retryable: true,
          });
          if (attempt < this.maxAttempts) {
            const waitMs = Math.min(8000, 350 * (2 ** (attempt - 1)) + Math.floor(this.random() * 250));
            await this.sleep(waitMs);
            continue;
          }
          throw lastError;
        }

        throw new DiscordHttpError(`Discord HTTP ${response.status}`, {
          status: response.status,
          code: response.status === 403 ? 'MISSING_PERMISSION' : response.status === 404 ? 'NOT_FOUND' : 'HTTP_ERROR',
          retryable: false,
        });
      } catch (error) {
        if (error instanceof DiscordHttpError) throw error;
        lastError = new DiscordHttpError('Discord transport failed', {
          code: error?.name === 'AbortError' ? 'TIMEOUT' : 'NETWORK_ERROR',
          retryable: true,
        });
        if (attempt < this.maxAttempts) {
          const waitMs = Math.min(8000, 350 * (2 ** (attempt - 1)) + Math.floor(this.random() * 250));
          await this.sleep(waitMs);
          continue;
        }
        throw lastError;
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastError || new DiscordHttpError('Discord request failed');
  }
}
