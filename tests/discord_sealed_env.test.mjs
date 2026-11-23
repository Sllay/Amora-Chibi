import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createCipheriv, diffieHellman, generateKeyPairSync, hkdfSync,
  randomBytes,
} from 'node:crypto';
import { writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { decryptDiscordEnv, loadSealedDiscordEnv } from '../workers/discord/env-bundle.mjs';

const FORMAT = 'amora-sealed-env-v1';
function makeEnvelope(values = {}) {
  const target = generateKeyPairSync('x25519');
  const ephemeral = generateKeyPairSync('x25519');
  const salt = randomBytes(16);
  const nonce = randomBytes(12);
  const shared = diffieHellman({ privateKey: ephemeral.privateKey, publicKey: target.publicKey });
  const aad = Buffer.from(FORMAT + ':discord');
  const key = Buffer.from(hkdfSync('sha256', shared, salt, aad, 32));
  const cipher = createCipheriv('aes-256-gcm', key, nonce);
  cipher.setAAD(aad);
  const plaintext = JSON.stringify({
    DISCORD_BOT_TOKEN: 'synthetic-sensitive-token',
    AMORA_MESSAGING_SERVICE_TOKEN: 'm'.repeat(40),
    ...values,
  });
  const ciphertext = Buffer.concat([
    cipher.update(Buffer.from(plaintext)), cipher.final(), cipher.getAuthTag(),
  ]);
  const ephemeralDer = ephemeral.publicKey.export({ type: 'spki', format: 'der' });
  return {
    privateKey: target.privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64'),
    envelope: {
      format: FORMAT,
      service: 'discord',
      ephemeral_public_key: ephemeralDer.subarray(-32).toString('base64'),
      salt: salt.toString('base64'),
      nonce: nonce.toString('base64'),
      ciphertext: ciphertext.toString('base64'),
    },
  };
}

test('sealed Discord env decrypts in memory and never needs a plaintext file', () => {
  const { privateKey, envelope } = makeEnvelope();
  const found = decryptDiscordEnv(Buffer.from(JSON.stringify(envelope)), privateKey);
  assert.equal(found.DISCORD_BOT_TOKEN, 'synthetic-sensitive-token');
  assert.equal(found.AMORA_MESSAGING_SERVICE_TOKEN.length, 40);
  assert.ok(!JSON.stringify(envelope).includes('synthetic-sensitive-token'));
});

test('tampering, wrong private key and wrong service are refused', () => {
  const { privateKey, envelope } = makeEnvelope();
  const other = makeEnvelope();
  assert.throws(() => decryptDiscordEnv(Buffer.from(JSON.stringify(envelope)), other.privateKey));
  const changed = structuredClone(envelope);
  changed.ciphertext = changed.ciphertext.slice(0, -2) + 'AA';
  assert.throws(() => decryptDiscordEnv(Buffer.from(JSON.stringify(changed)), privateKey));
  const swapped = { ...envelope, service: 'core' };
  assert.throws(() => decryptDiscordEnv(Buffer.from(JSON.stringify(swapped)), privateKey));
});

test('injection through environment variable names is rejected', () => {
  const { privateKey, envelope } = makeEnvelope({ NODE_OPTIONS: '--require=evil.js' });
  assert.throws(() => decryptDiscordEnv(Buffer.from(JSON.stringify(envelope)), privateKey));
});

test('wrong or missing key fails before worker startup', () => {
  const { envelope } = makeEnvelope();
  const dir = mkdtempSync(join(tmpdir(), 'amora-sealed-env-test-'));
  const path = join(dir, 'discord.json');
  try {
    writeFileSync(path, JSON.stringify(envelope));
    assert.throws(() => loadSealedDiscordEnv({}, path));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
