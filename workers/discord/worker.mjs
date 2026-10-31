import { MessagingCoreClient, workerConfig } from '../common/core-client.mjs';
import {
  DISCORD_INTENTS,
  discordReplyPayload,
  normalizeDiscordMessage,
  splitDiscordText,
} from './normalize.mjs';

const API = 'https://discord.com/api/v10';
const TRUE = new Set(['1', 'true', 'yes', 'on']);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const enabled = (name, fallback = false) => {
  const value = process.env[name];
  return value === undefined ? fallback : TRUE.has(value.trim().toLowerCase());
};

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 160)}` : '';
  process.stderr.write(`[amora-discord] ${code}${suffix}\n`);
}

async function discordRequest(token, path, { method = 'GET', body, timeoutMs = 15_000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response = await fetch(`${API}${path}`, {
      method,
      redirect: 'error',
      headers: {
        Authorization: `Bot ${token}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    if (response.status === 429) {
      let waitMs = 1000;
      try {
        const limited = await response.json();
        waitMs = Math.min(15_000, Math.max(250, Number(limited.retry_after || 1) * 1000));
      } catch {}
      await sleep(waitMs);
      response = await fetch(`${API}${path}`, {
        method,
        redirect: 'error',
        headers: {
          Authorization: `Bot ${token}`,
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    }
    const text = await response.text();
    let payload = null;
    if (text) {
      try { payload = JSON.parse(text); } catch {}
    }
    if (!response.ok) {
      const error = new Error(`Discord HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  } finally {
    clearTimeout(timer);
  }
}

export class DiscordWorker {
  constructor({ token, core, accountScope = 'primary', remoteProcessingAllowed = false }) {
    if (!token) throw new Error('DISCORD_BOT_TOKEN is required');
    this.token = token;
    this.core = core;
    this.accountScope = accountScope;
    this.remoteProcessingAllowed = remoteProcessingAllowed;
    this.socket = null;
    this.ready = false;
    this.stopped = false;
    this.sequence = null;
    this.sessionId = null;
    this.resumeGatewayUrl = null;
    this.botUserId = null;
    this.heartbeatTimer = null;
    this.heartbeatAcked = true;
    this.connectionGeneration = 0;
    this.reconnectRequested = false;
    this.processingOutbound = false;
  }

  async run() {
    safeLog('START');
    while (!this.stopped) {
      try {
        const presence = await this.core.getPresence();
        this.connectionGeneration = presence.generation;
        const shouldConnect = presence.desired_connected || presence.check_requested;
        if (shouldConnect && !this.socket) await this.connect(presence);
        if (!shouldConnect && this.socket) await this.disconnect('CORE_DESIRED_DISCONNECTED');
        if (this.ready) await this.flushOutbound();
      } catch (error) {
        safeLog('LOOP_ERROR', error?.name || 'Error');
      }
      await sleep(this.ready ? 800 : 2500);
    }
    await this.disconnect('WORKER_STOP');
  }

  stop() {
    this.stopped = true;
  }

  async connect(presence) {
    if (this.socket) return;
    await this.core.reportPresence('connecting', { generation: presence.generation }).catch(() => {});
    let gatewayUrl = this.resumeGatewayUrl;
    if (!gatewayUrl) {
      const gateway = await discordRequest(this.token, '/gateway/bot');
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
      this.handleGatewayData(event.data).catch((error) => safeLog('GATEWAY_EVENT_ERROR', error?.name || 'Error'));
    });
    socket.addEventListener('close', () => {
      if (this.socket !== socket) return;
      this.clearHeartbeat();
      this.socket = null;
      this.ready = false;
      this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
    });
    socket.addEventListener('error', () => safeLog('GATEWAY_SOCKET_ERROR'));
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
      if (!payload.d) {
        this.sessionId = null;
        this.resumeGatewayUrl = null;
        this.sequence = null;
      }
      await sleep(1000 + Math.floor(Math.random() * 1500));
      await this.forceReconnect('INVALID_SESSION');
      return;
    }
    if (payload.op !== 0) return;

    if (payload.t === 'READY') {
      this.sessionId = payload.d?.session_id || null;
      this.resumeGatewayUrl = payload.d?.resume_gateway_url || this.resumeGatewayUrl;
      this.botUserId = payload.d?.user?.id ? String(payload.d.user.id) : this.botUserId;
      this.ready = true;
      const presence = await this.core.reportPresence('connected', { generation: this.connectionGeneration });
      await this.completeRequestedCheck(presence);
      safeLog('READY');
      return;
    }
    if (payload.t === 'RESUMED') {
      this.ready = true;
      const presence = await this.core.reportPresence('connected', { generation: this.connectionGeneration });
      await this.completeRequestedCheck(presence);
      safeLog('RESUMED');
      return;
    }
    if (payload.t === 'MESSAGE_CREATE' && this.ready) {
      await this.handleMessageCreate(payload.d);
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
    setTimeout(() => {
      if (!this.socket) return;
      this.sendHeartbeat();
      this.heartbeatTimer = setInterval(() => this.sendHeartbeat(), intervalMs);
    }, first);
  }

  clearHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
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

  async handleMessageCreate(message) {
    let referencedAuthorId = message?.referenced_message?.author?.id || null;
    const referenceId = message?.message_reference?.message_id;
    if (!referencedAuthorId && referenceId && message?.channel_id) {
      try {
        const referenced = await discordRequest(
          this.token,
          `/channels/${encodeURIComponent(message.channel_id)}/messages/${encodeURIComponent(referenceId)}`,
          { timeoutMs: 5000 },
        );
        referencedAuthorId = referenced?.author?.id || null;
      } catch {}
    }
    const normalized = normalizeDiscordMessage(message, {
      botUserId: this.botUserId,
      accountScope: this.accountScope,
      referencedAuthorId,
      remoteProcessingAllowed: this.remoteProcessingAllowed,
    });
    if (!normalized) return;
    await this.core.inbound(normalized);
  }

  async flushOutbound() {
    if (this.processingOutbound) return;
    this.processingOutbound = true;
    try {
      const claimed = await this.core.claimOutbound(8);
      for (const item of claimed?.messages || []) {
        try {
          const externalMessageId = await this.sendOutbound(item);
          await this.core.ackOutbound(item.id, { sent: true, externalMessageId });
        } catch (error) {
          await this.core.ackOutbound(item.id, {
            sent: false,
            errorCode: `DISCORD_${error?.status || error?.name || 'SEND_FAILED'}`.slice(0, 120),
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
      const created = await discordRequest(
        this.token,
        `/channels/${encodeURIComponent(item.conversation_id)}/messages`,
        {
          method: 'POST',
          body: discordReplyPayload(chunks[index], {
            replyToMessageId: item.reply_to_message_id,
            firstChunk: index === 0,
          }),
        },
      );
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
  });
  process.on('SIGTERM', () => worker.stop());
  process.on('SIGINT', () => worker.stop());
  await worker.run();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    safeLog('FATAL', error?.name || 'Error');
    process.exitCode = 1;
  });
}
