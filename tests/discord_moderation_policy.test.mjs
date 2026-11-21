import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PERMISSION, moderationAuthorization, planClearPreview,
} from '../workers/discord/moderation-policy.mjs';

const ID = '111111111111111111';
const BOT = '222222222222222222';
const OTHER = '333333333333333333';
const ROLE = '444444444444444444';
const now = Date.UTC(2026, 10, 21, 17, 0, 0);
const fromTime = (ms, sequence = 0) =>
  String(((BigInt(ms) - 1420070400000n) << 22n) + BigInt(sequence));
const newer = (offset = 0) => fromTime(now - 60_000, offset);
const old = (offset = 0) => fromTime(now - 15 * 86_400_000, offset);
const perms = String(PERMISSION.VIEW_CHANNEL | PERMISSION.READ_MESSAGE_HISTORY |
  PERMISSION.MANAGE_MESSAGES);

test('guild clear is denied without actor and bot channel permissions', () => {
  const row = { id: newer(1), author: { id: OTHER } };
  assert.equal(planClearPreview([row], {
    channelType: 'guild', botUserId: BOT, actorPermissions: '0', botPermissions: perms, nowMs: now,
  }).reason, 'MODERATION_PERMISSIONS_REQUIRED');
  assert.equal(planClearPreview([row], {
    channelType: 'guild', botUserId: BOT, actorPermissions: perms,
    botPermissions: '0', nowMs: now,
  }).allowed, false);
});

test('guild preview separates 14-day batch and older single-delete candidates', () => {
  const rows = [
    { id: newer(1), author: { id: OTHER }, attachments: [{ id: '1' }] },
    { id: old(2), author: { id: OTHER }, attachments: [{ id: '2' }] },
    { id: newer(3), author: { id: OTHER }, pinned: true, attachments: [{ id: '3' }] },
    { id: newer(4), author: { id: ID }, attachments: [{ id: '4' }] },
  ];
  const preview = planClearPreview(rows, {
    channelType: 'guild', botUserId: BOT, actorPermissions: perms,
    botPermissions: perms, authorId: OTHER, media: 'attachments', nowMs: now,
  });
  assert.equal(preview.allowed, true);
  assert.deepEqual(preview.bulkIds, [newer(1)]);
  assert.deepEqual(preview.singleIds, [old(2)]);
  assert.equal(preview.skipped.pinned, 1);
  assert.equal(preview.requiresConfirmation, true);
  assert.equal(preview.executionPerformed, false);
});

test('DM preview only includes the bot own messages', () => {
  const rows = [
    { id: newer(1), author: { id: ID }, sticker_items: [{ id: '1' }] },
    { id: newer(2), author: { id: BOT }, sticker_items: [{ id: '2' }] },
  ];
  const preview = planClearPreview(rows, {
    channelType: 'dm', botUserId: BOT, media: 'stickers', nowMs: now,
  });
  assert.deepEqual(preview.bulkIds, [newer(2)]);
  assert.equal(preview.skipped.other_author_dm, 1);
  const prohibited = planClearPreview(rows, {
    channelType: 'dm', botUserId: BOT, authorId: ID, nowMs: now,
  });
  assert.equal(prohibited.reason, 'MODERATION_DM_OTHER_USER_DENIED');
});

test('role filters fail closed when author current role membership is unknown', () => {
  const rows = [
    { id: newer(1), author: { id: OTHER }, sticker_items: [{ id: '1' }] },
    { id: newer(2), author: { id: ID }, sticker_items: [{ id: '2' }] },
  ];
  const preview = planClearPreview(rows, {
    channelType: 'guild', botUserId: BOT, actorPermissions: perms,
    botPermissions: perms, roleId: ROLE, media: 'stickers', nowMs: now,
    roleIdsByAuthor: { [OTHER]: [ROLE] },
  });
  assert.deepEqual(preview.bulkIds, [newer(1)]);
  assert.equal(preview.skipped.unknown_role, 1);
});

test('mod actions require double permissions and hierarchy, timeout is limited', () => {
  const mod = String(PERMISSION.MODERATE_MEMBERS);
  const base = { inGuild: true, actorPermissions: mod, botPermissions: mod,
    actorRolePosition: 5, botRolePosition: 6, targetRolePosition: 2, durationSeconds: 3600 };
  assert.equal(moderationAuthorization('timeout', base).allowed, true);
  assert.equal(moderationAuthorization('timeout', { ...base,
    targetIsOwner: true }).allowed, false);
  assert.equal(moderationAuthorization('timeout', { ...base,
    targetIsAdmin: true }).allowed, false);
  assert.equal(moderationAuthorization('timeout', { ...base,
    botRolePosition: 1 }).reason, 'MODERATION_ROLE_HIERARCHY');
  assert.equal(moderationAuthorization('timeout', { ...base,
    durationSeconds: 29 * 24 * 3600 }).reason, 'MODERATION_TIMEOUT_RANGE_INVALID');
  assert.equal(moderationAuthorization('ban', { ...base,
    botPermissions: '0' }).reason, 'MODERATION_ACTOR_PERMISSION_DENIED');
});

test('preview rejects invalid filters and makes no REST calls', () => {
  assert.equal(planClearPreview([], {
    channelType: 'guild', botUserId: BOT,
    actorPermissions: perms, botPermissions: perms, limit: 1000,
  }).allowed, false);
  assert.equal(planClearPreview([], {
    channelType: 'dm', botUserId: BOT, roleId: ROLE,
  }).allowed, false);
});
