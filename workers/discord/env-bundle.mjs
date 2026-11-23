import { existsSync, readFileSync } from 'node:fs';
import {
  createDecipheriv, createPrivateKey, createPublicKey,
  diffieHellman, hkdfSync,
} from 'node:crypto';

const FORMAT = 'amora-sealed-env-v1';
const BUNDLE = '/opt/amora/secrets/discord.json';
const X25519_SPKI_PREFIX = Buffer.from('302a300506032b656e032100', 'hex');
const MAX_ENVELOPE = 65536;
const MAX_PLAIN = 32768;

export class SealedDiscordEnvError extends Error {
  constructor() { super('SEALED_DISCORD_ENV_INVALID'); }
}

function b64(value, maxBytes) {
  if (typeof value !== 'string' || value.length > maxBytes * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new SealedDiscordEnvError();
  }
  const result = Buffer.from(value, 'base64');
  if (result.length > maxBytes || result.toString('base64') !== value) {
    throw new SealedDiscordEnvError();
  }
  return result;
}

export function decryptDiscordEnv(envelopeBytes, privateKeyB64) {
  try {
    if (envelopeBytes.length > MAX_ENVELOPE) throw new SealedDiscordEnvError();
    const env = JSON.parse(Buffer.from(envelopeBytes).toString('utf8'));
    if (env?.format !== FORMAT || env?.service !== 'discord') {
      throw new SealedDiscordEnvError();
    }
    const privateKey = createPrivateKey({
      key: b64(privateKeyB64, 256), format: 'der', type: 'pkcs8',
    });
    if (privateKey.asymmetricKeyType !== 'x25519') throw new SealedDiscordEnvError();
    const pubRaw = b64(env.ephemeral_public_key, 32);
    const salt = b64(env.salt, 16);
    const nonce = b64(env.nonce, 12);
    const ciphertext = b64(env.ciphertext, MAX_ENVELOPE);
    if (pubRaw.length !== 32 || salt.length !== 16 || nonce.length !== 12 ||
        ciphertext.length < 17) throw new SealedDiscordEnvError();

    const ephemeral = createPublicKey({
      key: Buffer.concat([X25519_SPKI_PREFIX, pubRaw]),
      format: 'der', type: 'spki',
    });
    const shared = diffieHellman({ privateKey, publicKey: ephemeral });
    const aad = Buffer.from(FORMAT + ':discord');
    const aesKey = Buffer.from(hkdfSync('sha256', shared, salt, aad, 32));
    const encrypted = ciphertext.subarray(0, -16);
    const tag = ciphertext.subarray(-16);
    const decipher = createDecipheriv('aes-256-gcm', aesKey, nonce);
    decipher.setAAD(aad);
    decipher.setAuthTag(tag);
    const plain = Buffer.concat([decipher.update(encrypted), decipher.final()]);
    if (plain.length > MAX_PLAIN) throw new SealedDiscordEnvError();
    const values = JSON.parse(plain.toString('utf8'));
    if (!values || typeof values !== 'object' || Array.isArray(values)) {
      throw new SealedDiscordEnvError();
    }
    const entries = Object.entries(values);
    if (!entries.length || entries.length > 200) throw new SealedDiscordEnvError();
    if (!values.DISCORD_BOT_TOKEN || !values.AMORA_MESSAGING_SERVICE_TOKEN) {
      throw new SealedDiscordEnvError();
    }
    for (const [key, value] of entries) {
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(key) ||
          key.startsWith('ALTARE_') || key === 'AMORA_IMAGE_ENV_PRIVATE_KEY' ||
          ['__proto__', 'constructor', 'prototype', 'PATH', 'LD_PRELOAD',
           'NODE_OPTIONS', 'NODE_PATH', 'PYTHONPATH', 'PYTHONHOME', 'ENV', 'BASH_ENV'].includes(key) ||
          key.startsWith('LD_') || key.startsWith('GROQ_') || key.startsWith('OPENAI_') ||
          key.startsWith('AIVEN_') || key.startsWith('AMORA_POSTGRES_') ||
          typeof value !== 'string' || /[\u0000\r\n]/.test(value)) {
        throw new SealedDiscordEnvError();
      }
    }
    return Object.fromEntries(entries);
  } catch {
    throw new SealedDiscordEnvError();
  }
}

export function loadSealedDiscordEnv(env = process.env, path = BUNDLE) {
  const key = env.AMORA_IMAGE_ENV_PRIVATE_KEY;
  if (!existsSync(path)) {
    if (key) throw new SealedDiscordEnvError();
    return null;
  }
  if (!key) throw new SealedDiscordEnvError();
  const decoded = decryptDiscordEnv(readFileSync(path), key);
  return decoded;
}
