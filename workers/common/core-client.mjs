const DEFAULT_TIMEOUT_MS = 20_000;

function cleanBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' && url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') {
    throw new Error('AMORA_CORE_URL must use https outside localhost');
  }
  url.pathname = url.pathname.replace(/\/$/, '');
  url.search = '';
  url.hash = '';
  return url.toString().replace(/\/$/, '');
}

export class MessagingCoreError extends Error {
  constructor(message, {
    code = 'CORE_REQUEST_FAILED',
    status = null,
    payload = null,
    method = null,
    path = null,
  } = {}) {
    super(message);
    this.name = 'MessagingCoreError';
    this.code = code;
    this.status = status;
    this.payload = payload;
    this.method = method;
    this.path = path;
  }
}

export class MessagingCoreClient {
  constructor({ baseUrl, token, platform, accountScope = 'primary', timeoutMs = DEFAULT_TIMEOUT_MS }) {
    if (!token || token.length < 32) throw new Error('AMORA_MESSAGING_SERVICE_TOKEN must be at least 32 chars');
    this.baseUrl = cleanBaseUrl(baseUrl);
    this.token = token;
    this.platform = platform;
    this.accountScope = accountScope;
    this.timeoutMs = timeoutMs;
  }

  async request(path, { method = 'GET', body, timeoutMs = this.timeoutMs } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        redirect: 'error',
        headers: {
          Authorization: `Bearer ${this.token}`,
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await response.text();
      let payload = null;
      if (text) {
        try { payload = JSON.parse(text); } catch { payload = null; }
      }
      if (!response.ok) {
        throw new MessagingCoreError(`Core HTTP ${response.status}`, {
          code: `CORE_HTTP_${response.status}`,
          status: response.status,
          payload,
          method,
          path,
        });
      }
      return payload;
    } catch (error) {
      if (error instanceof MessagingCoreError) throw error;
      throw new MessagingCoreError('Core transport failed', {
        code: error?.name === 'AbortError' ? 'CORE_TIMEOUT' : 'CORE_NETWORK_ERROR',
        method,
        path,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  inbound(message) {
    return this.request('/v1/messaging/worker/inbound', { method: 'POST', body: message });
  }

  invalidateSource({ conversationId, nativeMessageId, reason, transportRevision = null }) {
    return this.request('/v1/messaging/worker/invalidate', {
      method: 'POST',
      body: {
        platform: this.platform,
        account_scope: this.accountScope,
        conversation_id: String(conversationId),
        native_message_id: String(nativeMessageId),
        reason: String(reason).slice(0, 120),
        transport_revision: transportRevision,
      },
    });
  }

  presenceControl({ action, senderIdentifiers, timeoutMs = 1800 }) {
    return this.request('/v1/messaging/worker/presence-control', {
      method: 'POST',
      timeoutMs,
      body: {
        platform: this.platform,
        account_scope: this.accountScope,
        action,
        sender_identifiers: senderIdentifiers,
      },
    });
  }

  claimOutbound(limit = 8) {
    const query = new URLSearchParams({
      platform: this.platform,
      account_scope: this.accountScope,
      limit: String(limit),
    });
    return this.request(`/v1/messaging/worker/outbound?${query}`);
  }

  ackOutbound(id, { sent, externalMessageId = null, errorCode = null }) {
    return this.request(`/v1/messaging/worker/outbound/${encodeURIComponent(id)}/ack`, {
      method: 'POST',
      body: {
        sent,
        external_message_id: externalMessageId,
        error_code: errorCode,
      },
    });
  }

  cancelOutbound(id, reason) {
    return this.request(`/v1/messaging/worker/outbound/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
      body: { reason: String(reason).slice(0, 120) },
    });
  }

  getPresence() {
    const query = new URLSearchParams({ account_scope: this.accountScope });
    return this.request(`/v1/messaging/worker/presence/${this.platform}?${query}`);
  }

  reportPresence(state, { generation = null, errorCode = null, checkComplete = false } = {}) {
    const query = new URLSearchParams({ account_scope: this.accountScope });
    return this.request(`/v1/messaging/worker/presence/${this.platform}/report?${query}`, {
      method: 'POST',
      body: {
        state,
        generation,
        error_code: errorCode,
        check_complete: checkComplete,
      },
    });
  }
}

export function workerConfig(platform) {
  const baseUrl = process.env.AMORA_CORE_URL || '';
  const token = process.env.AMORA_MESSAGING_SERVICE_TOKEN || '';
  const accountScope = process.env.AMORA_PLATFORM_ACCOUNT_SCOPE || 'primary';
  if (!baseUrl) throw new Error('AMORA_CORE_URL is required');
  return { baseUrl, token, platform, accountScope };
}
