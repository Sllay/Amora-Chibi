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

const TRUE = new Set(['1', 'true', 'yes', 'on']);
const TERMINAL_GATEWAY_CLOSE_CODES = new Set([4004, 4010, 4011, 4013, 4014]);
const NON_RESUMABLE_GATEWAY_CLOSE_CODES = new Set([4007, 4009]);
const RETRYABLE_CORE_STATUS = new Set([502, 503, 504]);
const READY_POLL_MS = 2_000;
const IDLE_POLL_MS = 2_500;
const LOOP_BACKOFF_MAX_MS = 15_000;
const INBOUND_RETRY_WINDOW_MS = 30_000;
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
  }

  async run() {
    safeLog('START', `remote_processing=${this.remoteProcessingAllowed}`);
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

  async deliverNormalized(message, { revision = 1, syntheticMessageId = null } = {}) {
    const normalized = normalizeDiscordMessage(message, {
      botUserId: this.botUserId,
      accountScope: this.accountScope,
      referencedAuthorId: await this.referencedAuthorId(message),
      remoteProcessingAllowed: this.remoteProcessingAllowed,
      transportRevision: revision,
      syntheticMessageId,
    });
    if (!normalized) return null;

    const started = Date.now();
    let attempt = 0;
    while (true) {
      attempt += 1;
      try {
        const result = await this.core.inbound(normalized);
        const action = result?.decision?.action || 'unknown';
        const outboundCount = Array.isArray(result?.outbound) ? result.outbound.length : 0;
        const reasons = Array.isArray(result?.decision?.reason_codes)
          ? result.decision.reason_codes.slice(0, 6).join(',')
          : 'none';
        safeLog('CORE_INBOUND_OK', `action=${action} outbound=${outboundCount} duplicate=${Boolean(result?.duplicate)} reasons=${reasons}`);
        if (outboundCount > 0 && this.ready) await this.flushOutbound();
        return result;
      } catch (error) {
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
  }

  isBotAuthored(message, previous = null) {
    const author = message?.author || previous?.message?.author || null;
    if (!author) return false;
    return Boolean(author.bot) || Boolean(this.botUserId && String(author.id) === String(this.botUserId));
  }

  async handleMessageCreate(message) {
    if (this.isBotAuthored(message)) return;
    const entry = this.runtime.recordCreate(message);
    if (!entry || entry.duplicate) return;
    await this.deliverNormalized(entry.message, { revision: entry.revision });
  }

  async handleMessageUpdate(partial) {
    const previous = this.runtime.state(partial?.channel_id, partial?.id);
    if (this.isBotAuthored(partial, previous)) {
      this.runtime.recordUpdate(partial);
      return;
    }
    const entry = this.runtime.recordUpdate(partial);
    if (!entry || entry.duplicate || !entry.semanticChanged) return;
    await this.core.invalidateSource({
      conversationId: partial.channel_id,
      nativeMessageId: partial.id,
      reason: 'DISCORD_MESSAGE_UPDATED',
      transportRevision: entry.revision,
    }).catch(() => {});
    await this.deliverNormalized(entry.message, {
      revision: entry.revision,
      syntheticMessageId: discordEditMessageId(partial.id, entry.revision),
    });
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
        const claimed = await this.core.claimOutbound(1, OUTBOUND_CLAIM_LEASE_SECONDS);
        const messages = claimed?.messages || [];
        if (!messages.length) break;
        safeLog('OUTBOUND_CLAIMED', messages.length);
        const item = messages[0];
        const validation = this.runtime.validateOutbound(item);
        if (!validation.ok) {
          await this.core.cancelOutbound(item.id, `DISCORD_${validation.reason}`).catch(() => {});
          continue;
        }

        let externalMessageId = null;
        try {
          externalMessageId = await this.sendOutbound(item);
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
          await this.core.ackOutbound(item.id, { sent: true, externalMessageId });
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

  async sendOutbound(item) {
    const chunks = splitDiscordText(item.text, 2000);
    if (!chunks.length) throw new Error('EMPTY_OUTBOUND');
    let firstMessageId = null;
    for (let index = 0; index < chunks.length; index += 1) {
      const created = await this.rest.request(
        `/channels/${encodeURIComponent(item.conversation_id)}/messages`,
        {
          method: 'POST',
          body: discordReplyPayload(chunks[index], {
            replyToMessageId: item.reply_to_message_id,
            firstChunk: index === 0,
            nonce: discordNonce(item.id, index),
          }),
        },
      );
      if (created?.id && created?.channel_id) this.runtime.rememberBotMessage(created);
      if (!firstMessageId) firstMessageId = created?.id || null;
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
