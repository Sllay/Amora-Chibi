/**
 * Pure Discord moderation policy and destructive-operation preview.
 *
 * No HTTP, no persistence, no user-generated commands, no automatic action.
 * A future slash-command handler must confirm scope/nonce and re-check live
 * actor+bot permissions immediately before executing each REST action.
 */
export const PERMISSION = Object.freeze({
  KICK_MEMBERS: 1n << 1n,
  BAN_MEMBERS: 1n << 2n,
  ADMINISTRATOR: 1n << 3n,
  VIEW_CHANNEL: 1n << 10n,
  MANAGE_MESSAGES: 1n << 13n,
  READ_MESSAGE_HISTORY: 1n << 16n,
  MODERATE_MEMBERS: 1n << 40n,
});
const DISCORD_EPOCH = 1420070400000n;
const BULK_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;
const MAX_PREVIEW_MESSAGES = 500;
const MAX_TIMEOUT_SECONDS = 28 * 24 * 60 * 60;
const snowflake = (value) => typeof value === 'string' && /^\d{16,22}$/.test(value);
const asBitfield = (value) => {
  try { return BigInt(value || 0); } catch { return 0n; }
};
const has = (value, flag) => {
  const bits = asBitfield(value);
  return (bits & PERMISSION.ADMINISTRATOR) !== 0n || (bits & flag) === flag;
};
const sentAtMs = (id) => snowflake(id) ? Number((BigInt(id) >> 22n) + DISCORD_EPOCH) : null;

export function moderationAuthorization(action, {
  inGuild = false, actorPermissions = '0', botPermissions = '0',
  actorRolePosition = 0, botRolePosition = 0, targetRolePosition = 0,
  actorIsOwner = false, targetIsOwner = false, targetIsAdmin = false,
  durationSeconds = null,
} = {}) {
  if (!inGuild) return { allowed: false, reason: 'MODERATION_GUILD_REQUIRED' };
  const required = {
    clear: PERMISSION.MANAGE_MESSAGES,
    timeout: PERMISSION.MODERATE_MEMBERS,
    kick: PERMISSION.KICK_MEMBERS,
    ban: PERMISSION.BAN_MEMBERS,
  }[action];
  if (!required) return { allowed: false, reason: 'MODERATION_ACTION_UNKNOWN' };
  if (!actorIsOwner && !has(actorPermissions, required)) {
    return { allowed: false, reason: 'MODERATION_ACTOR_PERMISSION_DENIED' };
  }
  if (!has(botPermissions, required)) {
    return { allowed: false, reason: 'MODERATION_BOT_PERMISSION_DENIED' };
  }
  if (action === 'clear') return { allowed: true, reason: 'MODERATION_PREVIEW_ALLOWED' };
  if (targetIsOwner || (action === 'timeout' && targetIsAdmin)) {
    return { allowed: false, reason: 'MODERATION_TARGET_PROTECTED' };
  }
  // The Discord bot cannot bypass role hierarchy, even if it is 'Admin'.
  if (botRolePosition <= targetRolePosition ||
      (!actorIsOwner && actorRolePosition <= targetRolePosition)) {
    return { allowed: false, reason: 'MODERATION_ROLE_HIERARCHY' };
  }
  if (action === 'timeout' &&
      (!Number.isInteger(durationSeconds) ||
       durationSeconds < 1 || durationSeconds > MAX_TIMEOUT_SECONDS)) {
    return { allowed: false, reason: 'MODERATION_TIMEOUT_RANGE_INVALID' };
  }
  return { allowed: true, reason: 'MODERATION_PREVIEW_ALLOWED' };
}

/**
 * Preview: the caller already retrieved this channel's messages with READ_HISTORY.
 * media='attachments' chooses messages with attachments or image/video embeds,
 * media='stickers' chooses messages with sticker objects. Entire messages are
 * selected because Discord cannot strip attachments from someone else's text.
 *
 * roleIdsByAuthor is a snapshot of CURRENT role membership, not historic roles.
 */
export function planClearPreview(messages, {
  channelType, botUserId, actorPermissions = '0',
  botPermissions = '0', authorId = null, roleId = null,
  roleIdsByAuthor = {}, media = 'all', includePinned = false,
  limit = 100, nowMs = Date.now(),
} = {}) {
  if (!['guild', 'dm'].includes(channelType) || !snowflake(botUserId)) {
    return { allowed: false, reason: 'MODERATION_SCOPE_INVALID' };
  }
  if (!['all', 'attachments', 'stickers'].includes(media) ||
      !Number.isInteger(limit) || limit < 1 || limit > MAX_PREVIEW_MESSAGES) {
    return { allowed: false, reason: 'MODERATION_FILTER_INVALID' };
  }
  if ((authorId != null && !snowflake(authorId)) ||
      (roleId != null && !snowflake(roleId))) {
    return { allowed: false, reason: 'MODERATION_FILTER_INVALID' };
  }
  if (channelType === 'guild' &&
      (!has(actorPermissions, PERMISSION.MANAGE_MESSAGES) ||
       !has(botPermissions, PERMISSION.MANAGE_MESSAGES) ||
       !has(botPermissions, PERMISSION.VIEW_CHANNEL) ||
       !has(botPermissions, PERMISSION.READ_MESSAGE_HISTORY))) {
    return { allowed: false, reason: 'MODERATION_PERMISSIONS_REQUIRED' };
  }
  if (channelType === 'dm' && (roleId != null ||
      (authorId != null && authorId !== botUserId))) {
    return { allowed: false, reason: 'MODERATION_DM_OTHER_USER_DENIED' };
  }
  if (!Array.isArray(messages)) {
    return { allowed: false, reason: 'MODERATION_HISTORY_INVALID' };
  }
  const bulkIds = [], singleIds = [];
  const seen = new Set();
  const skipped = { pinned: 0, unknown_role: 0, older: 0, other_author_dm: 0 };
  for (const item of messages.slice(0, MAX_PREVIEW_MESSAGES)) {
    if (!item || !snowflake(item.id) || seen.has(item.id)) continue;
    seen.add(item.id);
    const owner = String(item.author?.id || '');
    if (!snowflake(owner)) continue;
    if (channelType === 'dm' && owner !== botUserId) {
      skipped.other_author_dm += 1;
      continue;
    }
    if (authorId && owner !== authorId) continue;
    if (roleId) {
      const roles = roleIdsByAuthor[owner];
      if (!Array.isArray(roles)) {
        skipped.unknown_role += 1;
        continue;
      }
      if (!roles.includes(roleId)) continue;
    }
    if (item.pinned && !includePinned) { skipped.pinned += 1; continue; }
    if (media === 'attachments') {
      const attached = Array.isArray(item.attachments) && item.attachments.length > 0;
      const rich = Array.isArray(item.embeds) && item.embeds.some(
        (embed) => embed && ['image', 'video', 'gifv'].includes(embed.type),
      );
      if (!attached && !rich) continue;
    }
    if (media === 'stickers' &&
        !(Array.isArray(item.sticker_items) && item.sticker_items.length > 0)) continue;
    const timestamp = sentAtMs(item.id);
    if (timestamp === null || !Number.isFinite(timestamp) || timestamp > nowMs) continue;
    const old = nowMs - timestamp >= BULK_WINDOW_MS;
    if (old) { singleIds.push(item.id); skipped.older += 1; }
    else bulkIds.push(item.id);
    if (bulkIds.length + singleIds.length >= limit) break;
  }
  return {
    allowed: true,
    reason: 'MODERATION_PREVIEW_ONLY',
    count: bulkIds.length + singleIds.length,
    bulkIds, singleIds, skipped,
    scanned: Math.min(messages.length, MAX_PREVIEW_MESSAGES),
    // Never claim that older messages can be bulk deleted.
    bulkLimit: 100,
    requiresConfirmation: true,
    executionPerformed: false,
  };
}
