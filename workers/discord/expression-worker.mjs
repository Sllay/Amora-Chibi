import { MessagingCoreClient, workerConfig } from '../common/core-client.mjs';
import { discordReplyPayload, splitDiscordText } from './normalize.mjs';
import { DiscordWorker } from './worker.mjs';

const TRUE = new Set(['1', 'true', 'yes', 'on']);

const enabled = (name, fallback = false) => {
  const value = process.env[name];
  return value === undefined ? fallback : TRUE.has(value.trim().toLowerCase());
};

function safeLog(code, detail = '') {
  const suffix = detail ? ` ${String(detail).slice(0, 220)}` : '';
  process.stderr.write(`[amora-discord] ${code}${suffix}\n`);
}

function discordNonce(outboundId, chunkIndex) {
  const stable = String(outboundId || '').replace(/-/g, '').slice(0, 22).padEnd(22, '0');
  return `${stable}${Math.max(0, Number(chunkIndex) || 0).toString(36).padStart(2, '0')}`.slice(0, 25);
}

function outboundExpressions(item) {
  if (Array.isArray(item?.expressions) && item.expressions.length) return item.expressions;
  const stored = item?.metadata?.transport_expressions;
  return Array.isArray(stored) ? stored : [];
}

export class DiscordExpressionWorker extends DiscordWorker {
  async sendOutbound(item) {
    const chunks = splitDiscordText(item.text, 2000);
    const stickerIds = outboundExpressions(item)
      .filter((expression) => expression?.kind === 'sticker' && expression?.native_id)
      .map((expression) => String(expression.native_id))
      .slice(0, 1);

    const unitCount = Math.max(chunks.length, stickerIds.length ? 1 : 0);
    if (!unitCount) throw new Error('EMPTY_OUTBOUND');

    let firstMessageId = null;
    for (let index = 0; index < unitCount; index += 1) {
      const created = await this.rest.request(
        `/channels/${encodeURIComponent(item.conversation_id)}/messages`,
        {
          method: 'POST',
          body: discordReplyPayload(chunks[index] || '', {
            replyToMessageId: item.reply_to_message_id,
            firstChunk: index === 0,
            nonce: discordNonce(item.id, index),
            stickerIds: index === 0 ? stickerIds : [],
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
  const worker = new DiscordExpressionWorker({
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
