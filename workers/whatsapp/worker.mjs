import fs from 'node:fs/promises';
import path from 'node:path';

import makeWASocket, {
  DisconnectReason,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';

import { MessagingCoreClient, workerConfig } from '../common/core-client.mjs';
import {
  normalizeJidForCompare,
  normalizeWhatsAppMessage,
  shouldAcceptUpsert,
} from './normalize.mjs';

const TRUE = new Set(['1', 'true', 'yes', 'on']);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const enabled = (name, fallback = false) => {
  const value = process.env[name];
  return value === undefined ? fallback : TRUE.has(value.trim().toLowerCase());
};

const silentLogger = {
  level: 'silent',
  child() { return this; },
  trace() {},
  debug() {},
  info() {},
  warn() {},
  error() {},
  fatal() {},
};

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 160)}` : '';
  process.stderr.write(`[amora-whatsapp] ${code}${suffix}\n`);
}

function statusCode(error) {
  return (
    error?.output?.statusCode ||
    error?.data?.statusCode ||
    error?.statusCode ||
    error?.status ||
    null
  );
}

function cleanPhone(value) {
  const digits = String(value || '').replace(/\D/g, '');
  if (!digits || digits.length < 8 || digits.length > 15) return null;
  return digits;
}

function splitText(value, limit = 4000) {
  let remaining = String(value || '').trim();
  if (!remaining) return [];
  const chunks = [];
  while (remaining.length > limit) {
    let cut = remaining.lastIndexOf('\n', limit);
    if (cut < Math.floor(limit * 0.6)) cut = remaining.lastIndexOf(' ', limit);
    if (cut < Math.floor(limit * 0.5)) cut = limit;
    chunks.push(remaining.slice(0, cut).trimEnd());
    remaining = remaining.slice(cut).trimStart();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}

class RecentMessages {
  constructor(max = 512) {
    this.max = max;
    this.items = new Map();
  }

  put(message) {
    const id = message?.key?.id;
    if (!id) return;
    this.items.delete(String(id));
    this.items.set(String(id), message);
    while (this.items.size > this.max) {
      const first = this.items.keys().next().value;
      this.items.delete(first);
    }
  }

  get(id) {
    if (!id) return null;
    return this.items.get(String(id)) || null;
  }
}

export class WhatsAppWorker {
  constructor({ core, authDir, accountScope = 'primary', remoteProcessingAllowed = false }) {
    this.core = core;
    this.authDir = path.resolve(authDir);
    this.accountScope = accountScope;
    this.remoteProcessingAllowed = remoteProcessingAllowed;
    this.socket = null;
    this.saveCreds = null;
    this.ready = false;
    this.stopped = false;
    this.connectionGeneration = 0;
    this.recent = new RecentMessages();
    this.processingOutbound = false;
    this.loggedOut = false;
    this.pairingFile = path.join(this.authDir, 'pairing-secret.txt');
  }

  async run() {
    await fs.mkdir(this.authDir, { recursive: true, mode: 0o700 });
    await fs.chmod(this.authDir, 0o700).catch(() => {});
    safeLog('START');
    while (!this.stopped) {
      try {
        const presence = await this.core.getPresence();
        this.connectionGeneration = presence.generation;
        const shouldConnect = presence.desired_connected || presence.check_requested;
        if (shouldConnect && !this.socket && !this.loggedOut) await this.connect(presence);
        if (!shouldConnect && this.socket) await this.disconnect('CORE_DESIRED_DISCONNECTED');
        if (this.ready) await this.flushOutbound();
      } catch (error) {
        safeLog('LOOP_ERROR', error?.name || 'Error');
      }
      await sleep(this.ready ? 900 : 3000);
    }
    await this.disconnect('WORKER_STOP');
  }

  stop() {
    this.stopped = true;
  }

  async connect(presence) {
    if (this.socket) return;
    await this.core.reportPresence('connecting', { generation: presence.generation }).catch(() => {});
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);
    this.saveCreds = saveCreds;
    const socket = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, silentLogger),
      },
      logger: silentLogger,
      syncFullHistory: false,
      shouldSyncHistoryMessage: () => false,
      markOnlineOnConnect: false,
      emitOwnEvents: false,
      getMessage: async (key) => this.recent.get(key?.id)?.message,
    });
    this.socket = socket;

    socket.ev.on('creds.update', async () => {
      try { await saveCreds(); } catch { safeLog('AUTH_SAVE_FAILED'); }
    });
    socket.ev.on('connection.update', (update) => {
      this.handleConnectionUpdate(update, state).catch((error) => safeLog('CONNECTION_UPDATE_ERROR', error?.name || 'Error'));
    });
    socket.ev.on('messages.upsert', (event) => {
      this.handleUpsert(event).catch((error) => safeLog('UPSERT_ERROR', error?.name || 'Error'));
    });

    if (!state.creds.registered) {
      const phone = cleanPhone(process.env.AMORA_WHATSAPP_PHONE_NUMBER);
      if (phone) {
        try {
          const code = await socket.requestPairingCode(phone);
          await this.writePairingSecret(`PAIRING_CODE\n${code}\n`);
          safeLog('PAIRING_CODE_WRITTEN');
        } catch {
          safeLog('PAIRING_CODE_REQUEST_FAILED');
        }
      }
    }
  }

  async handleConnectionUpdate(update, authState) {
    if (update.qr && !authState.creds.registered) {
      await this.writePairingSecret(`QR_DATA\n${update.qr}\n`);
      safeLog('QR_DATA_WRITTEN');
    }
    if (update.connection === 'open') {
      this.ready = true;
      this.loggedOut = false;
      await fs.unlink(this.pairingFile).catch(() => {});
      const presence = await this.core.reportPresence('connected', { generation: this.connectionGeneration });
      await this.completeRequestedCheck(presence);
      safeLog('READY');
      return;
    }
    if (update.connection !== 'close') return;

    this.ready = false;
    const code = statusCode(update.lastDisconnect?.error);
    const loggedOut = code === DisconnectReason.loggedOut || code === 401;
    const current = this.socket;
    this.socket = null;
    if (loggedOut) {
      this.loggedOut = true;
      await this.core.reportPresence('error', {
        generation: this.connectionGeneration,
        errorCode: 'WHATSAPP_LOGGED_OUT_REPAIR_REQUIRED',
      }).catch(() => {});
      safeLog('LOGGED_OUT_REPAIR_REQUIRED');
      return;
    }
    await this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
    try { await current?.end?.(undefined); } catch {}
  }

  async disconnect(reason) {
    if (!this.socket) return;
    safeLog('DISCONNECT', reason);
    const socket = this.socket;
    this.socket = null;
    this.ready = false;
    try { await socket.end(undefined); } catch {}
    await this.core.reportPresence('disconnected', { generation: this.connectionGeneration }).catch(() => {});
  }

  async completeRequestedCheck(presence) {
    if (!presence.check_requested) return;
    await this.core.reportPresence('checking', { generation: presence.generation }).catch(() => {});
    await sleep(700);
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

  ownJids() {
    const values = [
      this.socket?.user?.id,
      this.socket?.user?.lid,
    ].filter(Boolean);
    return [...new Set(values.map(normalizeJidForCompare))];
  }

  async handleUpsert(event) {
    // Defense in depth for CVE-2026-48063. rc14 contains the upstream patch,
    // but requestId upserts and history/append events still do not qualify as
    // fresh social input for Amora.
    if (!shouldAcceptUpsert(event)) return;
    for (const message of event.messages) {
      if (message?.key?.fromMe) continue;
      this.recent.put(message);
      const normalized = normalizeWhatsAppMessage(message, {
        accountScope: this.accountScope,
        ownJids: this.ownJids(),
        remoteProcessingAllowed: this.remoteProcessingAllowed,
      });
      if (!normalized) continue;
      await this.core.inbound(normalized);
    }
  }

  async flushOutbound() {
    if (this.processingOutbound || !this.socket) return;
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
            errorCode: `WHATSAPP_${error?.name || 'SEND_FAILED'}`.slice(0, 120),
          }).catch(() => {});
        }
      }
    } finally {
      this.processingOutbound = false;
    }
  }

  async sendOutbound(item) {
    if (!this.socket) throw new Error('SOCKET_NOT_CONNECTED');
    const jid = item.conversation_id;
    const chunks = splitText(item.text);
    if (!chunks.length) throw new Error('EMPTY_OUTBOUND');
    const quoted = this.recent.get(item.reply_to_message_id);
    let firstId = null;
    for (let index = 0; index < chunks.length; index += 1) {
      const sent = await this.socket.sendMessage(
        jid,
        { text: chunks[index] },
        index === 0 && quoted ? { quoted } : {},
      );
      if (!firstId) firstId = sent?.key?.id || null;
    }
    return firstId;
  }

  async writePairingSecret(content) {
    await fs.mkdir(this.authDir, { recursive: true, mode: 0o700 });
    await fs.writeFile(this.pairingFile, content, { encoding: 'utf8', mode: 0o600 });
    await fs.chmod(this.pairingFile, 0o600).catch(() => {});
  }
}

async function main() {
  const config = workerConfig('whatsapp');
  const authDir = process.env.AMORA_WHATSAPP_AUTH_DIR || '';
  if (!authDir || !path.isAbsolute(authDir)) {
    throw new Error('AMORA_WHATSAPP_AUTH_DIR must be an absolute private durable path');
  }
  const core = new MessagingCoreClient(config);
  const worker = new WhatsAppWorker({
    core,
    authDir,
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
