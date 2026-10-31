function unwrapMessage(message) {
  let current = message || {};
  for (let depth = 0; depth < 5; depth += 1) {
    const nested =
      current.ephemeralMessage?.message ||
      current.viewOnceMessage?.message ||
      current.viewOnceMessageV2?.message ||
      current.viewOnceMessageV2Extension?.message ||
      current.documentWithCaptionMessage?.message;
    if (!nested) break;
    current = nested;
  }
  return current;
}

export function normalizeJidForCompare(value) {
  if (!value) return '';
  const text = String(value);
  const at = text.indexOf('@');
  if (at < 0) return text;
  const local = text.slice(0, at).replace(/:\d+$/, '');
  return `${local}${text.slice(at)}`;
}

function sameJid(left, right) {
  return normalizeJidForCompare(left) === normalizeJidForCompare(right);
}

function actorRefs(...values) {
  const seen = new Set();
  const refs = [];
  for (const raw of values.flat()) {
    const jid = normalizeJidForCompare(raw);
    if (!jid || seen.has(jid)) continue;
    seen.add(jid);
    const suffix = jid.split('@')[1] || '';
    refs.push({
      identifier_type: suffix === 'lid' ? 'whatsapp_lid' : 'whatsapp_jid',
      external_actor_id: jid,
      confidence: 1.0,
    });
    if (suffix === 's.whatsapp.net') {
      const digits = jid.split('@')[0].replace(/\D/g, '');
      if (digits) refs.push({
        identifier_type: 'phone_e164',
        external_actor_id: `+${digits}`,
        confidence: 0.98,
      });
    }
  }
  return refs.slice(0, 6);
}

function contextInfo(content) {
  for (const value of Object.values(content || {})) {
    if (value && typeof value === 'object' && value.contextInfo) return value.contextInfo;
  }
  return null;
}

function messageText(content) {
  return (
    content.conversation ||
    content.extendedTextMessage?.text ||
    content.imageMessage?.caption ||
    content.videoMessage?.caption ||
    content.documentMessage?.caption ||
    ''
  );
}

function numberLike(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value.toNumber === 'function') return value.toNumber();
  const parsed = Number(String(value));
  return Number.isFinite(parsed) ? parsed : null;
}

function attachmentList(messageId, content) {
  const specs = [
    ['imageMessage', 'image'],
    ['videoMessage', 'video'],
    ['audioMessage', 'audio'],
    ['documentMessage', 'document'],
    ['stickerMessage', 'sticker'],
  ];
  const items = [];
  for (const [key, kind] of specs) {
    const item = content[key];
    if (!item) continue;
    items.push({
      attachment_id: `${messageId}:${kind}`,
      media_type: item.mimetype || null,
      filename: item.fileName || null,
      size_bytes: numberLike(item.fileLength),
    });
  }
  return items;
}

function timestampIso(message) {
  const value = numberLike(message.messageTimestamp);
  if (!value) return new Date().toISOString();
  const millis = value > 10_000_000_000 ? value : value * 1000;
  return new Date(millis).toISOString();
}

export function normalizeWhatsAppMessage(message, {
  accountScope = 'primary',
  ownJids = [],
  remoteProcessingAllowed = false,
} = {}) {
  const key = message?.key || {};
  if (!key.id || !key.remoteJid || key.fromMe) return null;
  const content = unwrapMessage(message.message);
  const text = messageText(content);
  const attachments = attachmentList(String(key.id), content);
  if (!String(text).trim() && attachments.length === 0) return null;

  const isGroup = String(key.remoteJid).endsWith('@g.us');
  const actorJid = isGroup
    ? (key.participant || key.participantAlt || message.participant || key.remoteJid)
    : (key.remoteJidAlt || key.remoteJid);
  const alternateActor = isGroup ? key.participantAlt : key.remoteJidAlt;
  const senderIdentifiers = actorRefs(actorJid, alternateActor);
  if (!senderIdentifiers.length) return null;

  const context = contextInfo(content) || {};
  const mentions = Array.isArray(context.mentionedJid) ? context.mentionedJid.map(normalizeJidForCompare) : [];
  const own = ownJids.map(normalizeJidForCompare).filter(Boolean);
  const mentionsAmora = mentions.some((jid) => own.some((self) => sameJid(jid, self)));
  const quotedAuthor = context.participant || context.remoteJid || null;
  const replyToAmora = Boolean(quotedAuthor && own.some((self) => sameJid(quotedAuthor, self)));

  return {
    platform: 'whatsapp',
    account_scope: accountScope,
    conversation_id: normalizeJidForCompare(key.remoteJid),
    message_id: String(key.id),
    channel_type: isGroup ? 'group' : 'dm',
    sender_identifiers: senderIdentifiers,
    sender_display_name: message.pushName || null,
    sender_username: null,
    text: String(text || ''),
    mentioned_actor_ids: mentions.filter((jid) => !own.some((self) => sameJid(jid, self))),
    mentions_amora: mentionsAmora,
    reply_to_message_id: context.stanzaId ? String(context.stanzaId) : null,
    reply_to_amora: replyToAmora,
    reply_to_author_identifiers: quotedAuthor ? actorRefs(quotedAuthor) : [],
    attachments,
    timestamp: timestampIso(message),
    privacy_scope: 'personal',
    remote_processing_allowed: Boolean(remoteProcessingAllowed),
    native_thread_id: null,
    guild_or_group_id: isGroup ? normalizeJidForCompare(key.remoteJid) : null,
  };
}

export function shouldAcceptUpsert(event) {
  return Boolean(event && event.type === 'notify' && !event.requestId && Array.isArray(event.messages));
}
