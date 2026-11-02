export const DISCORD_INTENTS =
  (1 << 0) |  // GUILDS
  (1 << 9) |  // GUILD_MESSAGES
  (1 << 10) | // GUILD_MESSAGE_REACTIONS
  (1 << 12) | // DIRECT_MESSAGES
  (1 << 13) | // DIRECT_MESSAGE_REACTIONS
  (1 << 15);  // MESSAGE_CONTENT

export function discordEditMessageId(nativeMessageId, revision) {
  return `${String(nativeMessageId)}#edit:${Math.max(1, Number(revision) || 1)}`;
}

function expressionTags(value) {
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean).slice(0, 16);
  if (typeof value !== 'string') return [];
  return value.split(',').map((item) => item.trim()).filter(Boolean).slice(0, 16);
}

function normalizeSticker(item, guildId) {
  return {
    kind: 'sticker',
    native_id: String(item.id),
    name: item.name ? String(item.name).slice(0, 160) : null,
    description: item.description ? String(item.description).slice(0, 512) : null,
    tags: expressionTags(item.tags),
    format_type: Number.isFinite(item.format_type) ? Number(item.format_type) : null,
    guild_or_group_id: guildId,
  };
}

export function normalizeDiscordMessage(message, {
  botUserId,
  accountScope = 'primary',
  referencedAuthorId = null,
  remoteProcessingAllowed = false,
  transportRevision = 1,
  syntheticMessageId = null,
} = {}) {
  if (!message || !message.id || !message.channel_id || !message.author?.id) return null;
  if (message.author.bot || message.author.id === botUserId) return null;

  const mentions = Array.isArray(message.mentions) ? message.mentions : [];
  const rawAttachments = Array.isArray(message.attachments) ? message.attachments : [];
  const stickerItems = Array.isArray(message.sticker_items) ? message.sticker_items : [];
  const content = typeof message.content === 'string' ? message.content : '';
  if (!content.trim() && rawAttachments.length === 0 && stickerItems.length === 0) return null;

  const nativeMessageId = String(message.id);
  const referenceId = message.message_reference?.message_id || null;
  const resolvedReferenceAuthor = message.referenced_message?.author?.id || referencedAuthorId || null;
  const mentionedIds = mentions.map((item) => String(item.id)).filter(Boolean);
  const guildId = message.guild_id ? String(message.guild_id) : null;
  const expressions = stickerItems.slice(0, 8).map((item) => normalizeSticker(item, guildId));
  const catalogSource = Array.isArray(message.__amora_sticker_catalog) ? message.__amora_sticker_catalog : [];
  const availableExpressions = catalogSource
    .filter((item) => item?.id)
    .slice(0, 60)
    .map((item) => normalizeSticker(item, guildId));
  const attachments = [
    ...rawAttachments.slice(0, 16).map((item) => ({
      attachment_id: String(item.id),
      media_type: item.content_type || null,
      filename: item.filename || null,
      size_bytes: Number.isFinite(item.size) ? item.size : null,
    })),
    ...expressions.map((item) => ({
      attachment_id: `sticker:${item.native_id}`,
      media_type: 'application/vnd.discord.sticker',
      filename: item.name || 'discord-sticker',
      size_bytes: null,
    })),
  ].slice(0, 16);

  return {
    platform: 'discord',
    account_scope: accountScope,
    conversation_id: String(message.channel_id),
    message_id: syntheticMessageId ? String(syntheticMessageId) : nativeMessageId,
    native_message_id: nativeMessageId,
    transport_revision: Math.max(1, Number(transportRevision) || 1),
    channel_type: message.guild_id ? 'group' : 'dm',
    sender_identifiers: [
      {
        identifier_type: 'discord_user_id',
        external_actor_id: String(message.author.id),
        confidence: 1.0,
      },
    ],
    sender_display_name: message.member?.nick || message.author.global_name || message.author.username || null,
    sender_username: message.author.username || null,
    text: content,
    mentioned_actor_ids: mentionedIds.filter((id) => id !== botUserId),
    mentions_amora: Boolean(botUserId && mentionedIds.includes(botUserId)),
    reply_to_message_id: referenceId,
    reply_to_amora: Boolean(botUserId && resolvedReferenceAuthor === botUserId),
    reply_to_author_identifiers: resolvedReferenceAuthor
      ? [{ identifier_type: 'discord_user_id', external_actor_id: String(resolvedReferenceAuthor), confidence: 1.0 }]
      : [],
    attachments,
    expressions,
    available_expressions: availableExpressions,
    timestamp: message.edited_timestamp || message.timestamp || new Date().toISOString(),
    privacy_scope: 'PERSONAL',
    remote_processing_allowed: Boolean(remoteProcessingAllowed),
    native_thread_id: message.thread?.id || null,
    guild_or_group_id: guildId,
  };
}

export function normalizeDiscordReaction(event, { added = true } = {}) {
  if (!event?.message_id || !event?.channel_id || !event?.user_id) return null;
  return {
    platform: 'discord',
    conversation_id: String(event.channel_id),
    message_id: String(event.message_id),
    guild_or_group_id: event.guild_id ? String(event.guild_id) : null,
    actor_id: String(event.user_id),
    emoji: event.emoji?.id ? `id:${event.emoji.id}` : `name:${event.emoji?.name || ''}`,
    added: Boolean(added),
  };
}

export function splitDiscordText(text, limit = 2000) {
  const value = String(text || '').trim();
  if (!value) return [];
  const chunks = [];
  let remaining = value;
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

export function discordReplyPayload(text, {
  replyToMessageId = null,
  firstChunk = true,
  nonce = null,
  stickerIds = [],
} = {}) {
  const content = String(text || '');
  const stickers = [...new Set((stickerIds || []).map(String).filter(Boolean))].slice(0, 1);
  const payload = {
    allowed_mentions: { parse: [], replied_user: false },
  };
  if (content) payload.content = content;
  if (stickers.length) payload.sticker_ids = stickers;
  if (nonce) {
    payload.nonce = String(nonce).slice(0, 25);
    payload.enforce_nonce = true;
  }
  if (firstChunk && replyToMessageId) {
    payload.message_reference = {
      message_id: String(replyToMessageId),
      fail_if_not_exists: false,
    };
  }
  return payload;
}
