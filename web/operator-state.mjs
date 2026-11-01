export const OPERATOR_KEY = 'amora.web.operator.v1';

// Keep the administrative surface modular instead of growing the chat stylesheet.
if (typeof document !== 'undefined' && !document.querySelector('link[data-amora-operator-style]')) {
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = './operator.css?v=20261031-91';
  link.dataset.amoraOperatorStyle = '1';
  document.head.appendChild(link);
}

function decodePayload(value) {
  const match = /^browser\.v3\.([A-Za-z0-9_-]{1,256})\.[a-f0-9]{64}$/.exec(value || '');
  if (!match) return null;
  try {
    const payload = match[1].replace(/-/g, '+').replace(/_/g, '/');
    const decoded = atob(payload + '='.repeat((4 - payload.length % 4) % 4));
    const fields = decoded.split(':');
    if (fields.length !== 3 || fields[0] !== 'operator' || !/^\d{10,12}$/.test(fields[1]) || !/^[a-f0-9]{32}$/.test(fields[2])) return null;
    return { expiry: Number(fields[1]) };
  } catch (_) {
    return null;
  }
}

export function isOperatorCredential(value, nowMs = Date.now()) {
  const decoded = decodePayload(value);
  return Boolean(decoded && decoded.expiry * 1000 > nowMs);
}

export class OperatorState {
  constructor(storage) {
    this.storage = storage;
  }

  get() {
    let value = '';
    try { value = this.storage?.getItem(OPERATOR_KEY) || ''; } catch (_) {}
    if (isOperatorCredential(value)) return value;
    this.clear();
    return '';
  }

  save(token) {
    if (!isOperatorCredential(token)) throw new Error('credencial de operador inválida ou expirada');
    try {
      if (!this.storage) throw new Error('armazenamento local indisponível');
      this.storage.setItem(OPERATOR_KEY, token);
    } catch (error) {
      throw new Error(`não foi possível salvar o modo operador: ${error?.message || error}`);
    }
    return token;
  }

  clear() {
    try { this.storage?.removeItem(OPERATOR_KEY); } catch (_) {}
  }
}

if (typeof document !== 'undefined') {
  import('./operator-presence-authority.mjs?v=20261031-98')
    .then((module) => module.installPresenceAuthorityControl({ operatorKey: OPERATOR_KEY }))
    .catch(() => {});
  import('./dev-master-key.mjs?v=20261101-101')
    .then((module) => module.installDevMasterKey({ OperatorState }))
    .catch(() => {});
  import('./operator-recovery-controls.mjs?v=20261101-103')
    .then((module) => module.installOperatorRecoveryControls({ operatorKey: OPERATOR_KEY }))
    .catch(() => {});
}
