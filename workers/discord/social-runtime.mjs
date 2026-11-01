const DEFAULT_MAX_CHANNELS = 128;
const DEFAULT_MAX_MESSAGES_PER_CHANNEL = 256;
const DEFAULT_TTL_MS = 30 * 60 * 1000;

function nowMs(clock) {
  return Number(clock());
}

function channelKey(channelId) {
  return String(channelId || '');
}

function messageKey(messageId) {
  return String(messageId || '');
}

function cloneMessage(value) {
  if (!value || typeof value !== 'object') return value;
  return structuredClone(value);
}

function semanticFingerprint(message) {
  if (!message || typeof message !== 'object') return '';
  const mentions = (message.mentions || []).map((item) => String(item?.id || '')).filter(Boolean).sort();
  const attachments = (message.attachments || []).map((item) => ({
    id: String(item?.id || ''),
    content_type: item?.content_type || null,
    filename: item?.filename || null,
    size: Number(item?.size || 0),
  })).sort((left, right) => left.id.localeCompare(right.id));
  return JSON.stringify({
    content: String(message.content || ''),
    mentions,
    attachments,
    reply_to_message_id: message.message_reference?.message_id || null,
    reply_to_channel_id: message.message_reference?.channel_id || null,
    referenced_author_id: message.referenced_message?.author?.id || null,
  });
}

class ChannelSession {
  constructor({ id, guildId = null, parentId = null, now }) {
    this.id = id;
    this.guildId = guildId;
    this.parentId = parentId;
    this.generation = 0;
    this.lastActivityAt = now;
    this.messages = new Map();
    this.reactions = new Map();
  }
}

/**
 * Bounded native Discord state used only by the transport worker.
 *
 * It never decides canonical identity, memory, emotion or whether Amora should
 * answer. Its job is to know whether the exact Discord object that triggered a
 * Core reply still exists and still has the same semantic revision before bytes
 * are sent.
 */
export class DiscordSocialRuntime {
  constructor({
    clock = Date.now,
    maxChannels = DEFAULT_MAX_CHANNELS,
    maxMessagesPerChannel = DEFAULT_MAX_MESSAGES_PER_CHANNEL,
    ttlMs = DEFAULT_TTL_MS,
  } = {}) {
    this.clock = clock;
    this.maxChannels = Math.max(8, Number(maxChannels) || DEFAULT_MAX_CHANNELS);
    this.maxMessagesPerChannel = Math.max(32, Number(maxMessagesPerChannel) || DEFAULT_MAX_MESSAGES_PER_CHANNEL);
    this.ttlMs = Math.max(60_000, Number(ttlMs) || DEFAULT_TTL_MS);
    this.channels = new Map();
  }

  session(channelId, { guildId = null, parentId = null, create = true } = {}) {
    const id = channelKey(channelId);
    if (!id) return null;
    this.prune();
    let session = this.channels.get(id) || null;
    if (!session && create) {
      session = new ChannelSession({ id, guildId, parentId, now: nowMs(this.clock) });
      this.channels.set(id, session);
      this.pruneChannelCount();
    }
    if (session) {
      if (guildId) session.guildId = String(guildId);
      if (parentId) session.parentId = String(parentId);
      session.lastActivityAt = nowMs(this.clock);
      this.touchChannel(id, session);
    }
    return session;
  }

  recordCreate(message) {
    if (!message?.id || !message?.channel_id) return null;
    const session = this.session(message.channel_id, {
      guildId: message.guild_id || null,
      parentId: message.message_reference?.channel_id || null,
    });
    const id = messageKey(message.id);
    const previous = session.messages.get(id) || null;
    if (previous && !previous.deleted) {
      return { ...previous, duplicate: true, sessionGeneration: session.generation };
    }
    const revision = previous ? previous.revision + 1 : 1;
    const entry = {
      id,
      revision,
      deleted: false,
      message: cloneMessage(message),
      updatedAt: nowMs(this.clock),
    };
    session.messages.set(id, entry);
    session.generation += 1;
    this.trimMessages(session);
    return { ...entry, duplicate: false, sessionGeneration: session.generation };
  }

  recordUpdate(partial) {
    if (!partial?.id || !partial?.channel_id) return null;
    const session = this.session(partial.channel_id, { guildId: partial.guild_id || null });
    const id = messageKey(partial.id);
    const previous = session.messages.get(id) || null;
    const merged = {
      ...(previous?.message || {}),
      ...cloneMessage(partial),
      author: partial.author || previous?.message?.author || null,
      member: partial.member || previous?.message?.member || null,
      mentions: partial.mentions ?? previous?.message?.mentions ?? [],
      attachments: partial.attachments ?? previous?.message?.attachments ?? [],
      message_reference: partial.message_reference ?? previous?.message?.message_reference ?? null,
      referenced_message: partial.referenced_message ?? previous?.message?.referenced_message ?? null,
    };
    const previousContent = previous?.message?.content ?? null;
    const nextContent = merged.content ?? null;
    const contentChanged = partial.content !== undefined && previousContent !== nextContent;
    const semanticChanged = previous ? semanticFingerprint(previous.message) !== semanticFingerprint(merged) : true;
    if (previous && !previous.deleted && !semanticChanged) {
      const entry = {
        ...previous,
        message: merged,
        updatedAt: nowMs(this.clock),
      };
      session.messages.set(id, entry);
      return {
        ...entry,
        duplicate: true,
        contentChanged,
        semanticChanged: false,
        knownBefore: true,
        sessionGeneration: session.generation,
      };
    }
    const revision = (previous?.revision || 0) + 1;
    const entry = {
      id,
      revision,
      deleted: false,
      message: merged,
      updatedAt: nowMs(this.clock),
    };
    session.messages.set(id, entry);
    session.generation += 1;
    this.trimMessages(session);
    return {
      ...entry,
      duplicate: false,
      contentChanged,
      semanticChanged,
      knownBefore: Boolean(previous),
      sessionGeneration: session.generation,
    };
  }

  recordDelete(event) {
    if (!event?.id || !event?.channel_id) return null;
    const session = this.session(event.channel_id, { guildId: event.guild_id || null });
    const id = messageKey(event.id);
    const previous = session.messages.get(id) || null;
    if (previous?.deleted) {
      return { ...previous, duplicate: true, sessionGeneration: session.generation };
    }
    const entry = {
      id,
      revision: (previous?.revision || 0) + 1,
      deleted: true,
      message: previous?.message || { id, channel_id: String(event.channel_id), guild_id: event.guild_id || null },
      updatedAt: nowMs(this.clock),
    };
    session.messages.set(id, entry);
    session.generation += 1;
    return { ...entry, duplicate: false, sessionGeneration: session.generation };
  }

  recordBulkDelete(event) {
    const results = [];
    for (const id of event?.ids || []) {
      const item = this.recordDelete({ ...event, id });
      if (item) results.push(item);
    }
    return results;
  }

  recordReaction(event, added = true) {
    if (!event?.message_id || !event?.channel_id || !event?.user_id) return null;
    const session = this.session(event.channel_id, { guildId: event.guild_id || null });
    const emoji = event.emoji?.id ? `id:${event.emoji.id}` : `name:${event.emoji?.name || ''}`;
    const key = `${messageKey(event.message_id)}|${emoji}|${String(event.user_id)}`;
    if (added) session.reactions.set(key, nowMs(this.clock));
    else session.reactions.delete(key);
    session.generation += 1;
    return {
      messageId: messageKey(event.message_id),
      userId: String(event.user_id),
      emoji,
      added: Boolean(added),
      sessionGeneration: session.generation,
    };
  }

  state(channelId, messageId) {
    const session = this.session(channelId, { create: false });
    return session?.messages.get(messageKey(messageId)) || null;
  }

  validateOutbound(item) {
    const sourceId = item?.source_message_id || item?.reply_to_message_id || null;
    if (!sourceId) return { ok: true, reason: 'NO_SOURCE_MESSAGE' };
    const entry = this.state(item.conversation_id, sourceId);
    if (!entry) return { ok: true, reason: 'SOURCE_STATE_UNKNOWN' };
    if (entry.deleted) return { ok: false, reason: 'SOURCE_DELETED' };
    const expected = Number(item?.metadata?.transport_revision || 0);
    if (expected > 0 && entry.revision !== expected) {
      return { ok: false, reason: 'SOURCE_REVISION_CHANGED', currentRevision: entry.revision, expectedRevision: expected };
    }
    return { ok: true, reason: 'SOURCE_CURRENT', currentRevision: entry.revision };
  }

  rememberBotMessage(message) {
    return this.recordCreate(message);
  }

  snapshot(channelId) {
    const session = this.session(channelId, { create: false });
    if (!session) return null;
    return {
      channelId: session.id,
      guildId: session.guildId,
      parentId: session.parentId,
      generation: session.generation,
      messageCount: session.messages.size,
      reactionCount: session.reactions.size,
      lastActivityAt: session.lastActivityAt,
    };
  }

  prune() {
    const cutoff = nowMs(this.clock) - this.ttlMs;
    for (const [id, session] of this.channels) {
      if (session.lastActivityAt < cutoff) this.channels.delete(id);
    }
  }

  pruneChannelCount() {
    while (this.channels.size > this.maxChannels) {
      const oldest = this.channels.keys().next().value;
      if (oldest === undefined) break;
      this.channels.delete(oldest);
    }
  }

  trimMessages(session) {
    while (session.messages.size > this.maxMessagesPerChannel) {
      const oldest = session.messages.keys().next().value;
      if (oldest === undefined) break;
      session.messages.delete(oldest);
    }
  }

  touchChannel(id, session) {
    this.channels.delete(id);
    this.channels.set(id, session);
  }
}
