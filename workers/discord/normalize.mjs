export const DISCORD_INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15);

export function normalizeDiscordMessage(message, {
  botUserId,
  accountScope = 'primary',
  referencedAuthorId = null,
  remoteProcessingAllowed = false,
} = {}) {
  if (!message || !message.id || !message.channel_id || !message.author?.id) return null;
  if (message.author.bot || message.author.id === botUserId) return null;

  const mentions = Array.isArray(message.mentions) ? message.mentions : [];
  const attachments = Array.isArray(message.attachments) ? message.attachments : [];
  const content = typeof message.content === 'string' ? message.content : '';
  if (!content.trim() && attachments.length === 0) return null;

  const referenceId = message.message_reference?.message_id || null;
  const resolvedReferenceAuthor = message.referenced_message?.author?.id || referencedAuthorId || null;
  const mentionedIds = mentions.map((item) => String(item.id)).filter(Boolean);

  return {
    platform: 'discord',
    account_scope: accountScope,
    conversation_id: String(message.channel_id),
    message_id: String(message.id),
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
    attachments: attachments.slice(0, 16).map((item) => ({
      attachment_id: String(item.id),
      media_type: item.content_type || null,
      filename: item.filename || null,
      size_bytes: Number.isFinite(item.size) ? item.size : null,
    })),
    timestamp: message.timestamp || new Date().toISOString(),
    privacy_scope: 'personal',
    remote_processing_allowed: Boolean(remoteProcessingAllowed),
    native_thread_id: message.thread?.id || null,
    guild_or_group_id: message.guild_id ? String(message.guild_id) : null,
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

export function discordReplyPayload(text, { replyToMessageId = null, firstChunk = true } = {}) {
  const payload = {
    content: text,
    allowed_mentions: { parse: [], replied_user: false },
  };
  if (firstChunk && replyToMessageId) {
    payload.message_reference = {
      message_id: String(replyToMessageId),
      fail_if_not_exists: false,
    };
  }
  return payload;
}
