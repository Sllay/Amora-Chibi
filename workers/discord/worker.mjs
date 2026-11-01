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
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const enabled = (name, fallback = false) => {
  const value = process.env[name];
  return value === undefined ? fallback : TRUE.has(value.trim().toLowerCase());
};

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 160)}` : '';
  process.stderr.write(`[amora-discord] ${code}${suffix}\n`);
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
    safeLog('START');
    while (!this.stopped) {
      try {
        const presence = await this.core.getPresence();
        this.connectionGeneration = presence.generation;
        const shouldConnect = presence.desired_connected || presence.check_requested;
        if (shouldConnect && !this.socket && !this.terminalGatewayError) await this.connect(presence);
        if (!shouldConnect && this.socket) await this.disconnect('CORE_DESIRED_DISCONNECTED');
        if (this.ready) await this.flushOutbound();
      } catch (error) {
        safeLog('LOOP_ERROR', error?.code || error?.name || 'Error');
      }
      await sleep(this.ready ? 800 : 2500);
    }
    await this.disconnect('WORKER_STOP');
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
      this.handleGatewayData(event.data).catch((error) => safeLog('GATEWAY_EVENT_ERROR', error?.code || error?.name || 'Error'));
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
    await this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
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
      const presence = await this.core.reportPresence('connected', { generation: this.connectionGeneration });
      await this.ensureStagingCommand().catch((error) => safeLog('COMMAND_REGISTER_ERROR', error?.code || error?.status || error?.name || 'Error'));
      await this.completeRequestedCheck(presence);
      safeLog('READY');
      return;
    }
    if (payload.t === 'RESUMED') {
      this.ready = true;
      this.terminalGatewayError = null;
      const presence = await this.core.reportPresence('connected', { generation: this.connectionGeneration });
      await this.completeRequestedCheck(presence);
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

  async completeRequestedCheck(presence) {
    if (!presence.check_requested) return;
    await this.core.reportPresence('checking', { generation: presence.generation }).catch(() => {});
    await sleep(500);
    if (presence.desired_connected) {
      await this.core.reportPresence('connected', {
        generation: presence.generation,
        checkComplete: true,
      }).catch(() => {});
    } else {
      await this.core.reportPresence('disconnecting', {
        generation: presence.generation,
        checkComplete: true,
      }).catch(() => {});
      await this.disconnect('BOUNDED_CHECK_COMPLETE');
    }
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

    // Gateway interactions must receive an initial HTTP response within Discord's
    // deadline. Defer immediately, then resolve canonical identity/authority in
    // Core and edit the ephemeral original response.
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
      safeLog('INTERACTION_DEFER_FAILED', error?.code || error?.status || error?.name || 'Error');
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
        timeoutMs: 1800,
      });
      responseText = result?.response_text || responseText;
    } catch (error) {
      safeLog('INTERACTION_CORE_FAILED', error?.status || error?.name || 'Error');
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
      safeLog('INTERACTION_EDIT_FAILED', error?.code || error?.status || error?.name || 'Error');
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
    return this.core.inbound(normalized);
  }

  async handleMessageCreate(message) {
    const entry = this.runtime.recordCreate(message);
    if (!entry) return;
    await this.deliverNormalized(entry.message, { revision: entry.revision });
  }

  async handleMessageUpdate(partial) {
    const entry = this.runtime.recordUpdate(partial);
    if (!entry) return;
    await this.core.invalidateSource({
      conversationId: partial.channel_id,
      nativeMessageId: partial.id,
      reason: 'DISCORD_MESSAGE_UPDATED',
      transportRevision: entry.revision,
    }).catch(() => {});

    if (!entry.contentChanged) return;
    await this.deliverNormalized(entry.message, {
      revision: entry.revision,
      syntheticMessageId: discordEditMessageId(partial.id, entry.revision),
    });
  }

  async handleMessageDelete(event) {
    const entry = this.runtime.recordDelete(event);
    if (!entry) return;
    await this.core.invalidateSource({
      conversationId: event.channel_id,
      nativeMessageId: event.id,
      reason: 'DISCORD_MESSAGE_DELETED',
      transportRevision: entry.revision,
    }).catch(() => {});
  }

  async handleMessageBulkDelete(event) {
    const entries = this.runtime.recordBulkDelete(event);
    await Promise.all(entries.map((entry) => this.core.invalidateSource({
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
      const claimed = await this.core.claimOutbound(8);
      for (const item of claimed?.messages || []) {
        const validation = this.runtime.validateOutbound(item);
        if (!validation.ok) {
          await this.core.cancelOutbound(item.id, `DISCORD_${validation.reason}`).catch(() => {});
          continue;
        }
        try {
          const externalMessageId = await this.sendOutbound(item);
          await this.core.ackOutbound(item.id, { sent: true, externalMessageId });
        } catch (error) {
          await this.core.ackOutbound(item.id, {
            sent: false,
            errorCode: `DISCORD_${error?.code || error?.status || error?.name || 'SEND_FAILED'}`.slice(0, 120),
          }).catch(() => {});
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
    safeLog('FATAL', error?.code || error?.name || 'Error');
    process.exitCode = 1;
  });
}
