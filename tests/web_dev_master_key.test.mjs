import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import test from 'node:test';

import { signedBrowserCredential } from '../web/dev-master-key.mjs';

const MASTER = 'bk-personal-dev-master-key-32-bytes-minimum';

function decodePayload(token) {
  const [, version, payload, signature] = token.split('.');
  const raw = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64url').toString('utf8');
  return { version, payload, signature, raw };
}

function expectedSignature(version, payload) {
  return createHmac('sha256', MASTER).update(`browser.${version}.${payload}`).digest('hex');
}

test('dev master key creates a valid durable Web credential shape locally', async () => {
  const token = await signedBrowserCredential(MASTER, 'web');
  const parsed = decodePayload(token);
  assert.equal(parsed.version, 'v2');
  assert.match(parsed.raw, /^web:[a-f0-9]{32}$/);
  assert.equal(parsed.signature, expectedSignature(parsed.version, parsed.payload));
  assert.equal(token.includes(MASTER), false);
});

test('dev master key creates a bounded Operator credential shape locally', async () => {
  const before = Math.floor(Date.now() / 1000);
  const token = await signedBrowserCredential(MASTER, 'operator');
  const parsed = decodePayload(token);
  assert.equal(parsed.version, 'v3');
  const match = /^operator:(\d+):([a-f0-9]{32})$/.exec(parsed.raw);
  assert.ok(match);
  const expiry = Number(match[1]);
  assert.ok(expiry >= before + 12 * 60 * 60 - 2);
  assert.ok(expiry <= before + 12 * 60 * 60 + 2);
  assert.equal(parsed.signature, expectedSignature(parsed.version, parsed.payload));
  assert.equal(token.includes(MASTER), false);
});

test('dev master key refuses weak keys before creating a credential', async () => {
  await assert.rejects(() => signedBrowserCredential('short', 'web'), /32 bytes/);
});
