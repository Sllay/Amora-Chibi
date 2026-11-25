import { MessagingCoreClient, workerConfig } from '../common/core-client.mjs';
import {
  discordAmoraCommandDefinition,
  discordDeferredInteractionResponse,
  discordInteractionEdit,
  parseDiscordAmoraInteraction,
} from './commands.mjs';
import { DiscordRestClient } from './http.mjs';
import {
  DISCORD_INTENTS,
  discordEditMessageId,
  discordReplyPayload,
  normalizeDiscordMessage,
  normalizeDiscordReaction,
  splitDiscordText,
} from './normalize.mjs';
import { DiscordSocialRuntime } from './social-runtime.mjs';
import { collectDiscordIncomingImage, collectDiscordIncomingImages, discordImageCandidates, IncomingImageError } from './incoming-image.mjs';
import { transcodeOversizedImage } from './incoming-image-transform.mjs';
import { WebScreenshotService, screenshotMultipart } from './web-screenshot.mjs';
import { messagingTurnId, startTypingProgress } from './typing-progress.mjs';

const TRUE = new Set(['1', 'true', 'yes', 'on']);
const TERMINAL_GATEWAY_CLOSE_CODES = new Set([4004, 4010, 4011, 4013, 4014]);
const NON_RESUMABLE_GATEWAY_CLOSE_CODES = new Set([4007, 4009]);
const RETRYABLE_CORE_STATUS = new Set([502, 503, 504]);
const READY_POLL_MS = 2_000;
const IDLE_POLL_MS = 2_500;
const LOOP_BACKOFF_MAX_MS = 15_000;
const INBOUND_RETRY_WINDOW_MS = 30_000;
// 640 KiB WebP yields < 0.9 MiB JSON including Base64 and metadata.
// Only used after an observed HTTP 413; not a silent quality reduction.
const IMAGE_RETRY_WIRE_BYTES = 640 * 1024;
const IMAGE_PIPELINE_VERSION = 'ch2.5';
const OUTBOUND_CLAIM_LEASE_SECONDS = 120;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const enabled = (name, fallback = false) => {
  const value = process.env[name];
  return value === undefined ? fallback : TRUE.has(value.trim().toLowerCase());
};

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 220)}` : '';
  process.stderr.write(`[amora-discord] ${code}${suffix}\n`);
}

function traceUuid(value) {
  const id = String(value || '');
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : '-';
}

function traceDelivery(stage, item, metrics) {
  const values = Object.entries(metrics).map(([key, value]) => `${key}=${Math.round(Math.max(0, value))}`).join(' ');
  safeLog('PERFORMANCE_TRACE', `stage=${stage} outbound_id=${traceUuid(item?.id)} ${values}`);
}

function errorLabel(error) {
  if (error?.code) return String(error.code);
  if (error?.status) return `HTTP_${error.status}`;
  return String(error?.name || 'Error');
}

function isRetryableCoreError(error) {
  if (error?.code === 'CORE_NETWORK_ERROR') return true;
  return RETRYABLE_CORE_STATUS.has(Number(error?.status || 0));
}

function isDefinitiveDiscordSendFailure(error) {
  return error?.retryable === false;
}

function boundedRetryDelay(attempt) {
  const base = Math.min(5_000, 350 * (2 ** Math.min(5, Math.max(0, attempt - 1))));
  return base + Math.floor(Math.random() * 250);
}

function discordNonce(outboundId, chunkIndex) {
  const stable = String(outboundId || '').replace(/-/g, '').slice(0, 22).padEnd(22, '0');
  return `${stable}${Math.max(0, Number(chunkIndex) || 0).toString(36).padStart(2, '0')}`.slice(0, 25);
}

export class DiscordWorker {
  constructor({
    token,
    core,
    accountScope = 'primary',
    remoteProcessingAllowed = false,
    applicationId = null,
    testGuildId = null,
    rest = null,
    runtime = null,
  }) {
    if (!token) throw new Error('DISCORD_BOT_TOKEN is required');
    this.token = token;
    this.core = core;
    this.accountScope = accountScope;
    this.remoteProcessingAllowed = remoteProcessingAllowed;
    this.applicationId = applicationId ? String(applicationId) : null;
    this.testGuildId = testGuildId ? String(testGuildId) : null;
    this.registeredCommandGuild = null;
    this.rest = rest || new DiscordRestClient({ token });
    this.runtime = runtime || new DiscordSocialRuntime();
    this.webScreenshots = new WebScreenshotService({
      enabled: enabled('AMORA_WEB_SCREENSHOTS_ENABLED', true),
      apiKey: process.env.AMORA_WEB_SCREENSHOT_MICROLINK_API_KEY || '',
      dailyLimit: Number(process.env.AMORA_WEB_SCREENSHOT_DAILY_LIMIT || 4),
    });
    safeLog('WEB_SCREENSHOT_PIPELINE_READY', 'version=20261120-r2'
      + ' enabled=' + this.webScreenshots.enabled
      + ' provider=' + (this.webScreenshots.apiKey ? 'keyed' : 'keyless'));
    this.typingProgress = new Map();
    // Per-DM admission order for the synchronous Core path. No persistent
    // queue activation, no shared global lock, and no coalescing of turns.
    this.dmInboundTails = new Map();
    this.socket = null;
    this.ready = false;
    this.stopped = false;
    this.sequence = null;
    this.sessionId = null;
    this.resumeGatewayUrl = null;
    this.botUserId = null;
    this.heartbeatTimer = null;
    this.heartbeatStartTimer = null;
    this.heartbeatAcked = true;
    this.connectionGeneration = 0;
    this.reconnectRequested = false;
    this.processingOutbound = false;
    this.terminalGatewayError = null;
    this.recompressIncomingImage = transcodeOversizedImage;
  }

  async run() {
    safeLog('START', `remote_processing=${this.remoteProcessingAllowed} vision_pipeline=${IMAGE_PIPELINE_VERSION}`);
    let consecutiveFailures = 0;
    while (!this.stopped) {
      let delay = this.ready ? READY_POLL_MS : IDLE_POLL_MS;
      try {
        const presence = await this.core.getPresence();
        consecutiveFailures = 0;
        await this.syncPresence(presence);
      } catch (error) {
        consecutiveFailures += 1;
        safeLog('LOOP_ERROR', errorLabel(error));
        const exponential = 1_000 * (2 ** Math.min(4, consecutiveFailures - 1));
        delay = Math.min(LOOP_BACKOFF_MAX_MS, exponential) + Math.floor(Math.random() * 300);
      }
      await sleep(delay);
    }
    await this.disconnect('WORKER_STOP');
  }

  async syncPresence(presence) {
    this.connectionGeneration = presence.generation;
    const shouldConnect = presence.desired_connected || presence.check_requested;
    if (shouldConnect && !this.socket && !this.terminalGatewayError) {
      await this.connect(presence);
    }
    if (!shouldConnect && this.socket) {
      if (this.ready) await this.flushOutbound();
      await this.disconnect('CORE_DESIRED_DISCONNECTED');
      return;
    }

    if (this.ready && this.socket) {
      let current = presence;
      if (current.desired_connected && !['connected', 'checking'].includes(current.state)) {
        current = await this.reconcileReadyPresence(current);
      }
      if (current.check_requested) {
        await this.completeRequestedCheck(current);
      }
      if (this.ready) await this.flushOutbound();
    }
  }

  stop() {
    this.stopped = true;
  }

  async connect(presence) {
    if (this.socket || this.terminalGatewayError) return;
    await this.core.reportPresence('connecting', { generation: presence.generation }).catch(() => {});
    let gatewayUrl = this.resumeGatewayUrl;
    if (!gatewayUrl) {
      const gateway = await this.rest.request('/gateway/bot');
      gatewayUrl = gateway?.url;
    }
    if (!gatewayUrl) throw new Error('Discord gateway URL unavailable');
    const url = new URL(gatewayUrl);
    url.searchParams.set('v', '10');
    url.searchParams.set('encoding', 'json');

    this.reconnectRequested = false;
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.addEventListener('message', (event) => {
      this.handleGatewayData(event.data).catch((error) => safeLog('GATEWAY_EVENT_ERROR', errorLabel(error)));
    });
    socket.addEventListener('close', (event) => {
      this.handleGatewayClose(socket, event).catch(() => {});
    });
    socket.addEventListener('error', () => safeLog('GATEWAY_SOCKET_ERROR'));
  }

  async handleGatewayClose(socket, event) {
    if (this.socket !== socket) return;
    this.clearHeartbeat();
    this.socket = null;
    this.ready = false;
    const code = Number(event?.code || 0);

    if (NON_RESUMABLE_GATEWAY_CLOSE_CODES.has(code)) this.clearResumeState();
    if (TERMINAL_GATEWAY_CLOSE_CODES.has(code)) {
      this.clearResumeState();
      this.terminalGatewayError = `GATEWAY_CLOSE_${code}`;
      safeLog('GATEWAY_TERMINAL', code);
      await this.core.reportPresence('error', {
        generation: this.connectionGeneration,
        errorCode: this.terminalGatewayError,
      }).catch(() => {});
      return;
    }

    await this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
  }

  clearResumeState() {
    this.sessionId = null;
    this.resumeGatewayUrl = null;
    this.sequence = null;
  }

  async disconnect(reason) {
    if (!this.socket) return;
    safeLog('DISCONNECT', reason);
    const socket = this.socket;
    this.ready = false;
    this.clearHeartbeat();
    try { socket.close(1000, 'amora presence'); } catch {}
    if (this.socket === socket) this.socket = null;
    const snapshot = await this.core.reportPresence('disconnected', {
      generation: this.connectionGeneration,
    }).catch(() => null);
    if (snapshot?.generation !== undefined) this.connectionGeneration = snapshot.generation;
  }

  async handleGatewayData(raw) {
    let text = raw;
    if (raw instanceof ArrayBuffer) text = new TextDecoder().decode(raw);
    if (typeof Blob !== 'undefined' && raw instanceof Blob) text = await raw.text();
    if (typeof text !== 'string') return;
    const payload = JSON.parse(text);
    if (payload.s !== null && payload.s !== undefined) this.sequence = payload.s;

    if (payload.op === 10) {
      this.startHeartbeat(Number(payload.d?.heartbeat_interval || 45_000));
      this.identifyOrResume();
      return;
    }
    if (payload.op === 11) {
      this.heartbeatAcked = true;
      return;
    }
    if (payload.op === 1) {
      this.sendHeartbeat();
      return;
    }
    if (payload.op === 7) {
      await this.forceReconnect('GATEWAY_RECONNECT');
      return;
    }
    if (payload.op === 9) {
      if (!payload.d) this.clearResumeState();
      await sleep(1000 + Math.floor(Math.random() * 1500));
      await this.forceReconnect('INVALID_SESSION');
      return;
    }
    if (payload.op !== 0) return;

    if (payload.t === 'READY') {
      this.sessionId = payload.d?.session_id || null;
      this.resumeGatewayUrl = payload.d?.resume_gateway_url || this.resumeGatewayUrl;
      this.botUserId = payload.d?.user?.id ? String(payload.d.user.id) : this.botUserId;
      this.applicationId = this.applicationId || (payload.d?.application?.id ? String(payload.d.application.id) : null);
      this.ready = true;
      this.terminalGatewayError = null;
      await this.ensureStagingCommand().catch((error) => safeLog('COMMAND_REGISTER_ERROR', errorLabel(error)));
      await this.reconcileReadyPresence()
        .then((presence) => presence?.check_requested ? this.completeRequestedCheck(presence) : null)
        .catch((error) => safeLog('PRESENCE_RECONCILE_FAILED', errorLabel(error)));
      safeLog('READY');
      return;
    }
    if (payload.t === 'RESUMED') {
      this.ready = true;
      this.terminalGatewayError = null;
      await this.reconcileReadyPresence()
        .then((presence) => presence?.check_requested ? this.completeRequestedCheck(presence) : null)
        .catch((error) => safeLog('PRESENCE_RECONCILE_FAILED', errorLabel(error)));
      safeLog('RESUMED');
      return;
    }
    if (!this.ready) return;

    switch (payload.t) {
      case 'MESSAGE_CREATE':
        await this.handleMessageCreate(payload.d);
        break;
      case 'MESSAGE_UPDATE':
        await this.handleMessageUpdate(payload.d);
        break;
      case 'MESSAGE_DELETE':
        await this.handleMessageDelete(payload.d);
        break;
      case 'MESSAGE_DELETE_BULK':
        await this.handleMessageBulkDelete(payload.d);
        break;
      case 'MESSAGE_REACTION_ADD':
        this.handleReaction(payload.d, true);
        break;
      case 'MESSAGE_REACTION_REMOVE':
        this.handleReaction(payload.d, false);
        break;
      case 'INTERACTION_CREATE':
        await this.handleInteraction(payload.d);
        break;
      default:
        break;
    }
  }

  identifyOrResume() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    if (this.sessionId && this.sequence !== null) {
      this.socket.send(JSON.stringify({
        op: 6,
        d: { token: this.token, session_id: this.sessionId, seq: this.sequence },
      }));
      return;
    }
    this.socket.send(JSON.stringify({
      op: 2,
      d: {
        token: this.token,
        intents: DISCORD_INTENTS,
        properties: {
          os: process.platform,
          browser: 'amora-discord-worker',
          device: 'amora-discord-worker',
        },
      },
    }));
  }

  startHeartbeat(intervalMs) {
    this.clearHeartbeat();
    this.heartbeatAcked = true;
    const first = Math.floor(Math.random() * Math.max(1, intervalMs));
    this.heartbeatStartTimer = setTimeout(() => {
      this.heartbeatStartTimer = null;
      if (!this.socket) return;
      this.sendHeartbeat();
      this.heartbeatTimer = setInterval(() => this.sendHeartbeat(), intervalMs);
    }, first);
  }

  clearHeartbeat() {
    if (this.heartbeatStartTimer) clearTimeout(this.heartbeatStartTimer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatStartTimer = null;
    this.heartbeatTimer = null;
  }

  sendHeartbeat() {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    if (!this.heartbeatAcked) {
      this.forceReconnect('HEARTBEAT_ACK_MISSED').catch(() => {});
      return;
    }
    this.heartbeatAcked = false;
    this.socket.send(JSON.stringify({ op: 1, d: this.sequence }));
  }

  async forceReconnect(reason) {
    if (this.reconnectRequested) return;
    this.reconnectRequested = true;
    safeLog('RECONNECT', reason);
    const socket = this.socket;
    this.socket = null;
    this.ready = false;
    this.clearHeartbeat();
    try { socket?.close(4000, 'reconnect'); } catch {}
    await this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
  }

  async reconcileReadyPresence(presence = null, { refreshOnConflict = true } = {}) {
    if (!this.ready || !this.socket) return presence;
    let current = presence || await this.core.getPresence();
    this.connectionGeneration = current.generation;
    try {
      if (current.state === 'disconnecting' && current.desired_connected) {
        current = await this.core.reportPresence('disconnected', { generation: current.generation });
      }
      if (['disconnected', 'error'].includes(current.state) && (current.desired_connected || current.check_requested)) {
        current = await this.core.reportPresence('connecting', { generation: current.generation });
      }
      if (current.state === 'connecting') {
        current = await this.core.reportPresence('connected', { generation: current.generation });
      }
      if (current.state === 'checking' && !current.check_requested) {
        current = await this.core.reportPresence('connected', { generation: current.generation });
      }
      this.connectionGeneration = current.generation;
      return current;
    } catch (error) {
      if (refreshOnConflict && error?.code === 'CORE_HTTP_409') {
        safeLog('PRESENCE_GENERATION_REFRESH');
        const fresh = await this.core.getPresence();
        return this.reconcileReadyPresence(fresh, { refreshOnConflict: false });
      }
      throw error;
    }
  }

  async completeRequestedCheck(presence) {
    if (!presence?.check_requested || !this.ready || !this.socket) return presence;
    let current = presence;
    if (!['connected', 'checking'].includes(current.state)) {
      current = await this.reconcileReadyPresence(current);
    }
    if (current.state !== 'checking') {
      current = await this.core.reportPresence('checking', { generation: current.generation });
    }
    this.connectionGeneration = current.generation;
    await sleep(500);
    if (current.desired_connected) {
      current = await this.core.reportPresence('connected', {
        generation: current.generation,
        checkComplete: true,
      });
      this.connectionGeneration = current.generation;
      return current;
    }
    current = await this.core.reportPresence('disconnecting', {
      generation: current.generation,
      checkComplete: true,
    });
    this.connectionGeneration = current.generation;
    await this.disconnect('BOUNDED_CHECK_COMPLETE');
    return current;
  }

  async ensureStagingCommand() {
    if (!this.applicationId || !this.testGuildId || this.registeredCommandGuild === this.testGuildId) return;
    await this.rest.request(
      `/applications/${encodeURIComponent(this.applicationId)}/guilds/${encodeURIComponent(this.testGuildId)}/commands`,
      {
        method: 'POST',
        body: discordAmoraCommandDefinition(),
      },
    );
    this.registeredCommandGuild = this.testGuildId;
    safeLog('STAGING_COMMAND_READY');
  }

  async handleInteraction(interaction) {
    const command = parseDiscordAmoraInteraction(interaction);
    if (!command) return;

    try {
      await this.rest.request(
        `/interactions/${encodeURIComponent(command.interactionId)}/${encodeURIComponent(command.interactionToken)}/callback`,
        {
          method: 'POST',
          auth: false,
          maxAttempts: 1,
          timeoutMs: 1500,
          body: discordDeferredInteractionResponse({ ephemeral: true }),
        },
      );
    } catch (error) {
      safeLog('INTERACTION_DEFER_FAILED', errorLabel(error));
      return;
    }

    let responseText = 'Controle temporariamente indisponível.';
    try {
      const result = await this.core.presenceControl({
        action: command.action,
        senderIdentifiers: [
          {
            identifier_type: 'discord_user_id',
            external_actor_id: command.actorId,
            confidence: 1.0,
          },
        ],
        timeoutMs: 5000,
      });
      responseText = result?.response_text || responseText;
    } catch (error) {
      safeLog('INTERACTION_CORE_FAILED', errorLabel(error));
    }

    try {
      await this.rest.request(
        `/webhooks/${encodeURIComponent(command.applicationId)}/${encodeURIComponent(command.interactionToken)}/messages/@original`,
        {
          method: 'PATCH',
          auth: false,
          maxAttempts: 2,
          timeoutMs: 2500,
          body: discordInteractionEdit(responseText),
        },
      );
    } catch (error) {
      safeLog('INTERACTION_EDIT_FAILED', errorLabel(error));
    }
  }

  async referencedAuthorId(message) {
    let referencedAuthorId = message?.referenced_message?.author?.id || null;
    const referenceId = message?.message_reference?.message_id;
    if (!referencedAuthorId && referenceId && message?.channel_id) {
      try {
        const referenced = await this.rest.request(
          `/channels/${encodeURIComponent(message.channel_id)}/messages/${encodeURIComponent(referenceId)}`,
          { timeoutMs: 5000 },
        );
        referencedAuthorId = referenced?.author?.id || null;
      } catch {}
    }
    return referencedAuthorId;
  }

  beginTypingProgress(normalized) {
    if (normalized.channel_type !== 'dm') return () => {};
    const turnId = messagingTurnId(normalized);
    const log = (event, detail = '') => safeLog('TYPING_PROGRESS',
      `turn_id=${traceUuid(turnId)} event=${event}${detail ? ' ' + detail : ''}`);
    if (!this.ready || this.stopped || typeof this.core.progress !== 'function'
        || !enabled('AMORA_DISCORD_TYPING_PROGRESS', true)) {
      log('skipped', 'reason=not_ready_or_disabled');
      return () => {};
    }
    const channel = normalized.conversation_id;
    this.typingProgress.get(channel)?.('superseded');
    if (this.typingProgress.size >= 8) { log('skipped', 'reason=capacity'); return () => {}; }
    const isCurrent = () => {
      const validation = this.runtime.validateOutbound({
        conversation_id: channel, source_message_id: normalized.native_message_id,
        metadata: { transport_revision: normalized.transport_revision },
      });
      return this.ready && !this.stopped && validation.ok && validation.reason === 'SOURCE_CURRENT';
    };
    if (!isCurrent()) { log('skipped', 'reason=source_not_current'); return () => {}; }
    const stop = startTypingProgress({
      isCurrent,
      poll: async () => {
        const result = await this.core.progress(turnId);
        if (typeof result?.active !== 'boolean') {
          throw new Error('INVALID_PROGRESS_RESPONSE');
        }
        return result.active;
      },
      send: () => this.rest.request(`/channels/${encodeURIComponent(channel)}/typing`, {
        method: 'POST', timeoutMs: 2000, maxAttempts: 1,
      }),
      onEvent: (event, fields) => {
        const detail = Object.entries(fields).map(([key, value]) => `${key}=${value}`).join(' ');
        log(event, detail);
      },
      onStop: (reason) => {
        if (this.typingProgress.get(channel) === stop) this.typingProgress.delete(channel);
        log('stopped', `reason=${reason}`);
      },
    });
    this.typingProgress.set(channel, stop);
    log('started');
    return stop;
  }

  async deliverNormalized(message, { revision = 1, syntheticMessageId = null } = {}) {
    const normalizedStarted = performance.now();
    const normalized = normalizeDiscordMessage(message, {
      botUserId: this.botUserId,
      accountScope: this.accountScope,
      referencedAuthorId: await this.referencedAuthorId(message),
      remoteProcessingAllowed: this.remoteProcessingAllowed,
      transportRevision: revision,
      syntheticMessageId,
    });
    if (!normalized) return null;
    // Only DMs that allow remote processing carry ephemeral image bytes.
    // Core still checks the verified owner and response permission before Groq.
    if (this.remoteProcessingAllowed && normalized.channel_type === 'dm'
        && enabled('AMORA_DISCORD_IMAGE_VISION', true)) {
      const nativeAttachments = Array.isArray(message.attachments) ? message.attachments.slice(0, 16) : [];
      const likelyImage = nativeAttachments.find((item) => {
        const mime = String(item?.content_type || '').toLowerCase().split(';')[0];
        return mime.startsWith('image/') ||
          (/\.(?:png|jpe?g|webp|heic|heif|avif)$/i).test(String(item?.filename || ''));
      });
      if (likelyImage) {
        const announced = Number(likelyImage.size);
        const mib = Number.isFinite(announced) && announced >= 0
          ? Math.ceil(announced / (1024 * 1024)) : -1;
        safeLog('IMAGE_INGEST_BEGIN', `pipeline=${IMAGE_PIPELINE_VERSION} native_mib_ceil=${mib}`);
      }
      if (discordImageCandidates(message).length > 1) {
        const batch = await collectDiscordIncomingImages(message, {
          onStage: (stage) => safeLog('IMAGE_INGEST_STAGE', stage),
        });
        for (const candidate of discordImageCandidates(message)) {
          const item = batch.find(a => a.attachment_id === String(candidate.id));
          const nativeRef = normalized.attachments?.find(a => a.attachment_id === String(candidate.id));
          if (nativeRef && (!nativeRef.media_type || nativeRef.media_type === 'application/octet-stream')) {
            nativeRef.media_type = item?.image?.content_mime || 'image/unknown';
          }
        }
        normalized.incoming_images = batch;
      } else try {
        normalized.incoming_image = await collectDiscordIncomingImage(message, {
          onStage: (stage) => safeLog('IMAGE_INGEST_STAGE', stage),
        });
        if (normalized.incoming_image) {
          // Discord may omit content_type. Once magic bytes are verified,
          // fill in the safe media label on the same immutable attachment ID.
          const nativeRef = normalized.attachments?.find(
            (item) => item.attachment_id === normalized.incoming_image.attachment_id,
          );
          if (nativeRef && (!nativeRef.media_type
              || nativeRef.media_type === 'application/octet-stream')) {
            nativeRef.media_type = normalized.incoming_image.content_mime;
          }
          const mime = normalized.incoming_image.content_mime;
          const wireBytes = Math.floor(normalized.incoming_image.content_base64.length * 3 / 4);
          safeLog('IMAGE_INGEST_READY', `mime=${mime} wire_kib_ceil=${Math.ceil(wireBytes / 1024)}`);
        } else if (likelyImage) {
          normalized.incoming_image_status = 'IMAGE_UNSUPPORTED_FORMAT';
          safeLog('IMAGE_INGEST_SKIPPED', normalized.incoming_image_status);
        }
      } catch (error) {
        normalized.incoming_image_status = error instanceof IncomingImageError
          ? error.code : 'IMAGE_FETCH_FAILED';
        safeLog('IMAGE_INGEST_SKIPPED', normalized.incoming_image_status);
      }
    }
    const normalizationMs = performance.now() - normalizedStarted;
    const inboundStarted = performance.now();

    const stopTyping = this.beginTypingProgress(normalized);
    try {
      const started = Date.now();
      let attempt = 0;
      let image413Recovery = 0;
      while (true) {
        attempt += 1;
        try {
          const result = await this.core.inbound(normalized);
          stopTyping('inbound_complete');
          const action = result?.decision?.action || 'unknown';
          const outboundCount = Array.isArray(result?.outbound) ? result.outbound.length : 0;
          const reasons = Array.isArray(result?.decision?.reason_codes)
            ? result.decision.reason_codes.slice(0, 6).join(',')
            : 'none';
          safeLog('CORE_INBOUND_OK', `action=${action} outbound=${outboundCount} duplicate=${Boolean(result?.duplicate)} reasons=${reasons}`);
          traceDelivery('inbound', result?.outbound?.[0], { normalization_ms: normalizationMs, core_ms: performance.now() - inboundStarted, attempts: attempt });
          if (outboundCount > 0 && this.ready) await this.flushOutbound();
          return result;
        } catch (error) {
          // A gateway can reject a 4 MiB image after Base64 expands JSON
          // beyond its request-body cap. Never silently lose the entire turn.
          // Try one smaller visual derivative, then metadata-only delivery.
          if (Number(error?.status) === 413 && normalized.incoming_images?.some(item => item.image)) {
            // One metadata-only retry preserves ordering and caption without
            // dropping the message or retrying an oversized aggregate body.
            normalized.incoming_images = normalized.incoming_images.map(item => ({
              attachment_id:item.attachment_id,
              ingest_status:item.ingest_status || 'IMAGE_TRANSPORT_TOO_LARGE',
            }));
            safeLog('IMAGE_TRANSPORT_RECOVERY', 'reason=CORE_HTTP_413 action=batch_metadata_only');
            continue;
          }
          if (Number(error?.status) === 413 && normalized.incoming_image) {
            if (image413Recovery === 0) {
              image413Recovery = 1;
              safeLog('IMAGE_TRANSPORT_RECOVERY', 'reason=CORE_HTTP_413 action=recompress');
              try {
                const source = Buffer.from(normalized.incoming_image.content_base64, 'base64');
                const reduced = await this.recompressIncomingImage(
                  source, normalized.incoming_image.content_mime,
                  { maxBytes: IMAGE_RETRY_WIRE_BYTES },
                );
                if (!reduced || reduced.mime !== 'image/webp'
                    || !Buffer.isBuffer(reduced.bytes)
                    || reduced.bytes.length > IMAGE_RETRY_WIRE_BYTES
                    || reduced.bytes.length < 12) {
                  throw new IncomingImageError('IMAGE_TRANSPORT_RECOMPRESS_INVALID');
                }
                normalized.incoming_image = {
                  ...normalized.incoming_image,
                  content_mime: reduced.mime,
                  content_base64: reduced.bytes.toString('base64'),
                };
                safeLog('IMAGE_TRANSPORT_RECOVERY', 'action=retry_small_visual');
              } catch {
                // Core still receives original native attachment metadata.
                delete normalized.incoming_image;
                normalized.incoming_image_status = 'IMAGE_TRANSPORT_TOO_LARGE';
                safeLog('IMAGE_TRANSPORT_RECOVERY', 'action=metadata_only');
              }
              continue;
            }
            delete normalized.incoming_image;
            normalized.incoming_image_status = 'IMAGE_TRANSPORT_TOO_LARGE';
            safeLog('IMAGE_TRANSPORT_RECOVERY', 'reason=CORE_HTTP_413 action=metadata_only');
            continue;
          }
          const label = errorLabel(error);
          if (!isRetryableCoreError(error)) {
            safeLog('CORE_INBOUND_FAILED', label);
            throw error;
          }
          const delay = boundedRetryDelay(attempt);
          if ((Date.now() - started) + delay >= INBOUND_RETRY_WINDOW_MS) {
            safeLog('CORE_INBOUND_FAILED', `${label} retry_window_exhausted`);
            throw error;
          }
          safeLog('CORE_INBOUND_RETRY', `${label} attempt=${attempt + 1}`);
          await sleep(delay);
        }
      }
    } finally {
      stopTyping();
    }
  }

  isBotAuthored(message, previous = null) {
    const author = message?.author || previous?.message?.author || null;
    if (!author) return false;
    return Boolean(author.bot) || Boolean(this.botUserId && String(author.id) === String(this.botUserId));
  }

  async admitDiscordEntry(entry, options = {}, before = Promise.resolve()) {
    const message = entry.message;
    const channel = String(message?.channel_id || '');
    if (!channel || message?.guild_id) {
      await before;
      return this.deliverNormalized(message, options);
    }
    const previous = this.dmInboundTails.get(channel) || Promise.resolve();
    // Register synchronously before awaiting so later gateway events cannot
    // overtake this revision. Rejection of one turn must not poison the tail.
    const task = previous.catch(() => {}).then(async () => {
      await before;
      const state = this.runtime.state?.(channel, message.id);
      if (state && (state.deleted || state.revision !== entry.revision)) {
        safeLog('DM_INBOUND_REVISION_SUPERSEDED');
        return null;
      }
      return this.deliverNormalized(message, options);
    });
    this.dmInboundTails.set(channel, task);
    try {
      return await task;
    } finally {
      if (this.dmInboundTails.get(channel) === task) this.dmInboundTails.delete(channel);
    }
  }

  async handleMessageCreate(message) {
    if (this.isBotAuthored(message)) return;
    const entry = this.runtime.recordCreate(message);
    if (!entry || entry.duplicate) return;
    await this.admitDiscordEntry(entry, { revision: entry.revision });
  }

  async handleMessageUpdate(partial) {
    const previous = this.runtime.state(partial?.channel_id, partial?.id);
    if (this.isBotAuthored(partial, previous)) {
      this.runtime.recordUpdate(partial);
      return;
    }
    const entry = this.runtime.recordUpdate(partial);
    if (!entry || entry.duplicate || !entry.semanticChanged) return;
    // Invalidation begins immediately, even if another turn is generating.
    // Reserve the edited turn's admission position before waiting for the ACK.
    const invalidation = this.core.invalidateSource({
      conversationId: partial.channel_id,
      nativeMessageId: partial.id,
      reason: 'DISCORD_MESSAGE_UPDATED',
      transportRevision: entry.revision,
    }).catch(() => {});
    await this.admitDiscordEntry(entry, {
      revision: entry.revision,
      syntheticMessageId: discordEditMessageId(partial.id, entry.revision),
    }, invalidation);
  }

  async handleMessageDelete(event) {
    const previous = this.runtime.state(event?.channel_id, event?.id);
    const botAuthored = this.isBotAuthored(null, previous);
    const entry = this.runtime.recordDelete(event);
    if (!entry || entry.duplicate || botAuthored) return;
    await this.core.invalidateSource({
      conversationId: event.channel_id,
      nativeMessageId: event.id,
      reason: 'DISCORD_MESSAGE_DELETED',
      transportRevision: entry.revision,
    }).catch(() => {});
  }

  async handleMessageBulkDelete(event) {
    const botIds = new Set(
      (event?.ids || []).filter((id) => this.isBotAuthored(null, this.runtime.state(event.channel_id, id))).map(String),
    );
    const entries = this.runtime.recordBulkDelete(event);
    const relevant = entries.filter((entry) => !entry.duplicate && !botIds.has(String(entry.id)));
    await Promise.all(relevant.map((entry) => this.core.invalidateSource({
      conversationId: event.channel_id,
      nativeMessageId: entry.id,
      reason: 'DISCORD_MESSAGE_BULK_DELETED',
      transportRevision: entry.revision,
    }).catch(() => {})));
  }

  handleReaction(event, added) {
    const normalized = normalizeDiscordReaction(event, { added });
    if (!normalized || normalized.actor_id === this.botUserId) return;
    this.runtime.recordReaction(event, added);
  }

  async flushOutbound() {
    if (this.processingOutbound) return;
    this.processingOutbound = true;
    try {
      for (let drained = 0; drained < 8; drained += 1) {
        const claimStarted = performance.now();
        const claimed = await this.core.claimOutbound(1, OUTBOUND_CLAIM_LEASE_SECONDS, true);
        const messages = claimed?.messages || [];
        if (!messages.length) break;
        safeLog('OUTBOUND_CLAIMED', messages.length);
        const item = messages[0];
        traceDelivery('claim', item, { claim_ms: performance.now() - claimStarted });
        const validation = this.runtime.validateOutbound(item);
        if (!validation.ok) {
          await this.core.cancelOutbound(item.id, `DISCORD_${validation.reason}`).catch(() => {});
          continue;
        }

        if (item?.metadata?.discord_delivery_claim) {
          try {
            await this.sendCheckpointedOutbound(item);
          } catch (error) {
            safeLog('OUTBOUND_SEGMENT_PENDING', errorLabel(error));
            break;
          }
          continue;
        }
        let externalMessageId = null;
        const sendStarted = performance.now();
        try {
          externalMessageId = await this.sendOutbound(item);
          traceDelivery('send', item, { send_ms: performance.now() - sendStarted });
        } catch (error) {
          const label = errorLabel(error);
          safeLog('OUTBOUND_SEND_FAILED', label);
          if (isDefinitiveDiscordSendFailure(error)) {
            await this.core.ackOutbound(item.id, {
              sent: false,
              errorCode: `DISCORD_${label}`.slice(0, 120),
            }).catch(() => {});
            continue;
          }
          // 429/5xx/network/timeout can have unknown final delivery outcome. Keep
          // the row claimed; after lease expiry the same deterministic Discord
          // nonce is retried and Discord can return the already-created message.
          safeLog('OUTBOUND_SEND_OUTCOME_UNKNOWN', label);
          break;
        }

        try {
          const ackStarted = performance.now();
          await this.core.ackOutbound(item.id, { sent: true, externalMessageId });
          traceDelivery('ack', item, { ack_ms: performance.now() - ackStarted });
          safeLog('OUTBOUND_SENT');
        } catch (error) {
          // Discord already accepted the message. Never convert an ACK transport
          // failure into sent=false. Leave it claimed and recover by nonce after
          // lease expiry if necessary.
          safeLog('OUTBOUND_ACK_FAILED', errorLabel(error));
          break;
        }
      }
    } finally {
      this.processingOutbound = false;
    }
  }

  async sendCheckpointedOutbound(item) {
    const claim = item.metadata.discord_delivery_claim;
    const {lease, chunks, receipts} = claim;
    if (item.platform !== 'discord' || typeof lease !== 'string'
        || !Array.isArray(chunks) || chunks.length < 2 || chunks.length > 32
        || chunks.some(chunk => typeof chunk !== 'string' || !chunk.trim() || chunk.length > 2000)
        || !Array.isArray(receipts) || receipts.length >= chunks.length
        || receipts.some(id => !/^[0-9]{17,20}$/.test(id))) {
      throw new Error('DISCORD_DELIVERY_CHECKPOINT_INVALID');
    }
    const path = `/channels/${encodeURIComponent(item.conversation_id)}/messages`;
    for (let index = receipts.length; index < chunks.length; index++) {
      const segment = {lease, index, phase:'prepare'};
      await this.core.ackOutbound(item.id,{sent:false,segment});
      let created;
      try {
        created = await this.rest.request(path,{method:'POST',body:discordReplyPayload(chunks[index],{
          firstChunk:index===0,replyToMessageId:item.reply_to_message_id,nonce:discordNonce(item.id,index),
        })});
      } catch (error) {
        if (!isDefinitiveDiscordSendFailure(error)) throw error;
        await this.core.ackOutbound(item.id,{sent:false,
          errorCode:`DISCORD_${errorLabel(error)}`.slice(0,120),segment:{...segment,phase:'confirm'}});
        return;
      }
      if (!/^[0-9]{17,20}$/.test(String(created?.id || ''))
          || String(created?.channel_id) !== String(item.conversation_id)) {
        throw new Error('DISCORD_SEGMENT_RECEIPT_INVALID');
      }
      this.runtime.rememberBotMessage(created);
      await this.core.ackOutbound(item.id,{sent:true,externalMessageId:String(created.id),
        segment:{...segment,phase:'confirm'}});
    }
    safeLog('OUTBOUND_SEGMENTS_SENT');
  }

  async sendOutbound(item) {
    const chunks = splitDiscordText(item.text, 2000);
    if (!chunks.length) throw new Error('EMPTY_OUTBOUND');
    // The Core minted this flag after verified owner DM consent. A user/LLM
    // cannot smuggle screenshot destinations into the worker's text path.
    // Backward-compatible with a previous Core during independent deploys:
    // its verified screenshot source already implies an explicit owner request.
    const verifiedSource = item?.metadata?.web_capture_owner_verified === true
      && typeof item?.metadata?.web_capture_source === 'string';
    const screenshotRequested = item?.platform === 'discord'
      && (item?.metadata?.web_capture_requested === true || verifiedSource);
    const authorizedScreenshot = screenshotRequested && verifiedSource;
    const captureReplyHint = /^(?:solicitei a captura|vou solicitar o envio do print)/i
      .test(String(item?.text || '').trim());
    if (captureReplyHint && !screenshotRequested) {
      // A Core capture-status message with no transport metadata must not
      // silently masquerade as a screenshot attempt. Never infer a URL from text.
      safeLog('WEB_SCREENSHOT_METADATA_MISSING');
      const note = '\n\n(O pedido de print chegou sem autorização de anexo. Nada foi enviado.)';
      if (chunks[0].length + note.length <= 2000) chunks[0] += note;
      else chunks.push(note.trim());
    }
    safeLog('WEB_SCREENSHOT_DISPATCH', 'requested=' + screenshotRequested
      + ' authorized=' + authorizedScreenshot);
    const screenshot = authorizedScreenshot
      ? await this.webScreenshots.capture(item.metadata.web_capture_source)
      : null;
    if (screenshotRequested && !screenshot) {
      const knownFailures = new Set([
        'disabled', 'invalid_public_url', 'daily_limit',
        'provider_rate_limited', 'provider_auth_required',
        'provider_http_error', 'provider_response_invalid', 'provider_refused',
        'provider_image_url_invalid', 'image_cdn_http_error',
        'image_mime_invalid', 'image_bytes_invalid', 'provider_timeout',
        'provider_network_error', 'provider_unknown',
      ]);
      const rawCode = authorizedScreenshot ? this.webScreenshots.lastFailureCode : null;
      const reason = authorizedScreenshot
        ? (knownFailures.has(rawCode) ? rawCode : 'provider_unknown')
        : 'no_authorized_public_page';
      safeLog('WEB_SCREENSHOT_UNAVAILABLE', 'reason=' + reason);
      const note = authorizedScreenshot
        ? '\n\n(Não consegui capturar o print. Código: ' + reason
          + '. Nenhum anexo foi enviado.)'
        : '\n\n(Não existe uma página pública autorizada para este print. Nenhum anexo foi enviado.)';
      if (chunks[0].length + note.length <= 2000) chunks[0] += note;
      else chunks.push(note.trim());
    }
    let firstMessageId = null;
    for (let index = 0; index < chunks.length; index += 1) {
      const payload = discordReplyPayload(chunks[index], {
        replyToMessageId: item.reply_to_message_id,
        firstChunk: index === 0,
        nonce: discordNonce(item.id, index),
      });
      const messagePath = `/channels/${encodeURIComponent(item.conversation_id)}/messages`;
      let created;
      let imageUploadRejected = false;
      try {
        created = await this.rest.request(messagePath, {
          method: 'POST',
          ...(index === 0 && screenshot
            ? { multipart: screenshotMultipart(payload, screenshot) }
            : { body: payload }),
        });
      } catch (error) {
        // A definitive 4xx means the screenshot upload was rejected and no
        // message was accepted. Deliver links as JSON instead of silencing
        // the reply. For uncertain network/5xx outcomes, do NOT duplicate
        // the message: let the existing nonce/ack recovery own retry.
        if (!(index === 0 && screenshot && error?.retryable === false
            && [400, 413, 415, 422].includes(Number(error?.status)))) throw error;
        imageUploadRejected = true;
        safeLog('WEB_SCREENSHOT_UPLOAD_REJECTED', 'status=' + error.status);
        // The raster was fetched successfully, but Discord rejected the
        // multipart. Surface a *verified* Microlink CDN URL when present so
        // the owner can still open the actual screenshot. A link is not an
        // attachment; do not claim otherwise.
        const trustedFallback = typeof screenshot.imageUrl === 'string'
          && /^https:\/\/(?:(?:cdn|[a-z]{3}|[a-z]{3}\d)\.microlink\.io|microlink-cdn\.s3\.amazonaws\.com)\//.test(screenshot.imageUrl)
          ? screenshot.imageUrl : '';
        const note = '\n\n(O Discord recusou o anexo do print. '
          + (trustedFallback ? 'Captura disponível pelo link verificado: ' + trustedFallback
            : 'A captura não foi entregue.') + ')';
        const fallbackPayload = {
          ...payload,
          content: (payload.content + note).slice(0, 2000),
        };
        created = await this.rest.request(messagePath, {
          method: 'POST', body: fallbackPayload,
        });
      }
      if (created?.id && created?.channel_id) this.runtime.rememberBotMessage(created);
      if (!firstMessageId) firstMessageId = created?.id || null;
      const attachmentConfirmed = created?.id && String(created?.channel_id) === String(item.conversation_id)
        && Array.isArray(created.attachments) && created.attachments.some(
          (entry) => entry?.id && entry?.filename === screenshot?.filename
            && (entry.size == null || entry.size === screenshot?.bytes?.byteLength)
        );
      if (index === 0 && screenshot && !imageUploadRejected && !attachmentConfirmed) {
        // Accepted text is not proof of an attached image. Never retry the
        // uncertain image request: send a clearly labeled status instead.
        safeLog('WEB_SCREENSHOT_ATTACHMENT_NOT_CONFIRMED');
        const correction = discordReplyPayload(
          'Não consegui confirmar o anexo do print na resposta do Discord. ' +
          (typeof screenshot.imageUrl === 'string' &&
           /^https:\/\/(?:(?:cdn|[a-z]{3}|[a-z]{3}\d)\.microlink\.io|microlink-cdn\.s3\.amazonaws\.com)\//.test(screenshot.imageUrl)
            ? 'É possível abrir a captura neste link: ' + screenshot.imageUrl
            : 'Considere que a captura não foi entregue; os links continuam válidos.'),
          { nonce: discordNonce(item.id, chunks.length), firstChunk: true },
        );
        try {
          const notice = await this.rest.request(messagePath, {
            method: 'POST', body: correction,
          });
          if (notice?.id && notice?.channel_id) this.runtime.rememberBotMessage(notice);
        } catch (noticeError) {
          safeLog('WEB_SCREENSHOT_RECEIPT_NOTICE_FAILED', errorLabel(noticeError));
        }
      } else if (index === 0 && screenshot && !imageUploadRejected) {
        safeLog('WEB_SCREENSHOT_ATTACHMENT_CONFIRMED');
      }
    }
    return firstMessageId;
  }
}

async function main() {
  const config = workerConfig('discord');
  const token = process.env.DISCORD_BOT_TOKEN || '';
  const core = new MessagingCoreClient(config);
  const worker = new DiscordWorker({
    token,
    core,
    accountScope: config.accountScope,
    remoteProcessingAllowed: enabled('AMORA_PLATFORM_REMOTE_PROCESSING', false),
    applicationId: process.env.DISCORD_APPLICATION_ID || null,
    testGuildId: process.env.DISCORD_TEST_GUILD_ID || null,
  });
  process.on('SIGTERM', () => worker.stop());
  process.on('SIGINT', () => worker.stop());
  await worker.run();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    safeLog('FATAL', errorLabel(error));
    process.exitCode = 1;
  });
}
