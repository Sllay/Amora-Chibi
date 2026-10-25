// Visual state belongs to this browser, not to Core memory or the cognitive DB.
export const PAIRING_KEY = 'amora.web.pairing.v2';
export const HISTORY_KEY = 'amora.web.history.v1';
const LEGACY_TOKEN_KEY = 'amora.web.token.v1';
const MAX_ROLE_MESSAGES = 50;
const MAX_MESSAGES = 100;
const MAX_TEXT_LENGTH = 12000;

export function browserStorage(name) {
  try { return globalThis[name]; } catch (_) { return null; }
}

export function isWebCredential(value) {
  if (typeof value !== 'string') return false;
  const match = /^browser\.v2\.([A-Za-z0-9_-]{1,256})\.[a-f0-9]{64}$/.exec(value);
  if (!match) return false;
  try {
    const payload = match[1].replace(/-/g, '+').replace(/_/g, '/');
    return /^web:[a-f0-9]{32}$/.test(atob(payload + '='.repeat((4 - payload.length % 4) % 4)));
  } catch (_) { return false; }
}

function containsCredential(text) {
  // Never persist recognized credential material even when pasted into chat.
  return /\bBearer\s+\S+|browser\.v[12]\.[A-Za-z0-9_-]+\.[a-f0-9]{64}|\b(?:gsk_|sk-or-v1-|sk-proj-|AIza)[A-Za-z0-9_-]{16,}|\b(?:LOCAL_API_TOKEN|[A-Z_]*(?:API_KEY|AUTH_TOKEN|SERVICE_TOKEN))\s*[:=]\s*\S+/i.test(text);
}

function validMessage(item) {
  return item !== null && typeof item === 'object' && !Array.isArray(item)
    && Object.keys(item).every(key => ['role', 'text', 'id'].includes(key))
    && ['user', 'amora'].includes(item.role)
    && typeof item.text === 'string' && item.text.trim().length > 0
    && item.text.length <= MAX_TEXT_LENGTH && !containsCredential(item.text)
    && (item.id === undefined || (typeof item.id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(item.id)));
}

function prune(messages) {
  const counts = { user: 0, amora: 0 };
  const kept = [];
  const ids = new Set();
  for (let index = messages.length - 1; index >= 0; index--) {
    const item = messages[index];
    const identity = item.id ? `${item.role}:${item.id}` : null;
    if (identity && ids.has(identity)) continue;
    if (counts[item.role] >= MAX_ROLE_MESSAGES) continue;
    kept.push(item);
    counts[item.role]++;
    if (identity) ids.add(identity);
  }
  return kept.reverse().slice(-MAX_MESSAGES);
}

export class BrowserState {
  constructor(storage, sessionStorage = null) {
    this.storage = storage;
    // Old Web values could be administrative credentials. Never migrate them.
    this.remove(LEGACY_TOKEN_KEY);
    try { sessionStorage?.removeItem(LEGACY_TOKEN_KEY); } catch (_) {}
    this.messages = this.loadHistory();
  }

  get(key) {
    try { return this.storage?.getItem(key) ?? null; } catch (_) { return null; }
  }

  set(key, value) {
    try {
      if (!this.storage) return false;
      this.storage.setItem(key, value);
      return true;
    } catch (_) { return false; }
  }

  remove(key) {
    try { this.storage?.removeItem(key); } catch (_) {}
  }

  pairing() {
    const token = this.get(PAIRING_KEY);
    if (isWebCredential(token)) return token;
    this.remove(PAIRING_KEY);
    return '';
  }

  savePairing(token) {
    // Shape checking prevents storing an admin/Avatar/provider credential. Only
    // the Core can authenticate the signature; every request still goes to it.
    if (!isWebCredential(token)) throw new Error('credencial Web limitada inválida');
    if (!this.set(PAIRING_KEY, token)) throw new Error('armazenamento local indisponível para salvar pareamento');
    return token;
  }

  forgetPairing() {
    this.remove(PAIRING_KEY);
  }

  loadHistory() {
    const raw = this.get(HISTORY_KEY);
    if (raw === null) return [];
    try {
      if (raw.length > 2000000) throw new Error('oversized history');
      const data = JSON.parse(raw);
      if (!data || data.version !== 1 || Object.keys(data).some(key => !['version', 'messages'].includes(key))
          || !Array.isArray(data.messages) || !data.messages.every(validMessage)) throw new Error('invalid history');
      const messages = prune(data.messages);
      this.set(HISTORY_KEY, JSON.stringify({ version: 1, messages }));
      return messages;
    } catch (_) {
      this.remove(HISTORY_KEY);
      return [];
    }
  }

  hasMessage(role, id) {
    return Boolean(id && this.messages.some(item => item.role === role && item.id === id));
  }

  appendMessage(role, text, id = undefined, privacyScope = null) {
    if (['AUTH', 'SECRET'].includes(privacyScope)) return false;
    const item = { role, text, ...(id ? { id } : {}) };
    if (!validMessage(item) || this.hasMessage(role, id)) return false;
    this.messages = prune([...this.messages, item]);
    this.set(HISTORY_KEY, JSON.stringify({ version: 1, messages: this.messages }));
    return true;
  }

  clearHistory() {
    this.messages = [];
    this.remove(HISTORY_KEY);
  }
}
