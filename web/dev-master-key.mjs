import { BrowserState, browserStorage } from './browser-state.mjs';

const encoder = new TextEncoder();
const WEB_TTL_SECONDS = null;
const OPERATOR_TTL_SECONDS = 12 * 60 * 60;

function bytesToHex(bytes) {
  return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
}

function base64Url(text) {
  const bytes = encoder.encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function randomNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return bytesToHex(bytes);
}

async function hmacHex(secret, message) {
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
  return bytesToHex(new Uint8Array(signature));
}

async function signedBrowserCredential(masterKey, role) {
  if (!globalThis.crypto?.subtle || !globalThis.crypto?.getRandomValues) {
    throw new Error('este navegador não oferece Web Crypto seguro');
  }
  const bytes = encoder.encode(masterKey);
  if (bytes.length < 32) throw new Error('a chave pessoal precisa ter pelo menos 32 bytes');
  if (bytes.length > 512) throw new Error('a chave pessoal é grande demais');
  if ([...masterKey].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
    throw new Error('a chave pessoal não pode conter caracteres de controle');
  }

  const nonce = randomNonce();
  let version;
  let raw;
  if (role === 'web') {
    version = 'v2';
    raw = `web:${nonce}`;
  } else if (role === 'operator') {
    version = 'v3';
    const expiry = Math.floor(Date.now() / 1000) + OPERATOR_TTL_SECONDS;
    raw = `operator:${expiry}:${nonce}`;
  } else {
    throw new Error('papel de credencial não suportado');
  }
  const payload = base64Url(raw);
  const prefix = `browser.${version}.`;
  const signature = await hmacHex(masterKey, prefix + payload);
  return prefix + payload + '.' + signature;
}

async function preflight(path, token) {
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  if (response.ok) return;
  if (response.status === 401 || response.status === 403) {
    throw new Error('chave pessoal inválida ou não configurada no Core');
  }
  const text = await response.text();
  throw new Error(text || `falha HTTP ${response.status}`);
}

function makeMasterForm({ id, label, buttonLabel }) {
  const wrapper = document.createElement('div');
  wrapper.className = 'dev-master-bootstrap';

  const note = document.createElement('p');
  note.className = 'subtle';
  note.textContent = 'Ou use sua chave pessoal Dev. Ela assina a credencial localmente e não é enviada ao Core.';

  const form = document.createElement('form');
  form.id = id;
  form.className = 'pair-form';

  const input = document.createElement('input');
  input.type = 'password';
  input.autocomplete = 'current-password';
  input.maxLength = 512;
  input.placeholder = 'Chave pessoal Dev';
  input.setAttribute('aria-label', label);

  const button = document.createElement('button');
  button.type = 'submit';
  button.textContent = buttonLabel;

  const error = document.createElement('p');
  error.className = 'error';
  error.setAttribute('aria-live', 'polite');

  form.append(input, button);
  wrapper.append(note, form, error);
  return { wrapper, form, input, button, error };
}

function installWebMasterPairing() {
  const pairForm = document.getElementById('pairForm');
  if (!pairForm || document.getElementById('devMasterWebForm')) return;
  const browserState = new BrowserState(
    browserStorage('localStorage'),
    browserStorage('sessionStorage'),
  );
  const ui = makeMasterForm({
    id: 'devMasterWebForm',
    label: 'Chave pessoal para pareamento Web',
    buttonLabel: 'Parear com minha chave',
  });
  pairForm.insertAdjacentElement('afterend', ui.wrapper);
  ui.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    ui.error.textContent = '';
    ui.button.disabled = true;
    const masterKey = ui.input.value;
    try {
      const token = await signedBrowserCredential(masterKey, 'web');
      await preflight('/v1/web/state', token);
      browserState.savePairing(token);
      ui.input.value = '';
      location.reload();
    } catch (error) {
      ui.error.textContent = error?.message || String(error);
    } finally {
      ui.button.disabled = false;
    }
  });
}

function installOperatorMasterUnlock(OperatorState) {
  const pairForm = document.getElementById('operatorPairForm');
  if (!pairForm || document.getElementById('devMasterOperatorForm')) return;
  const operatorState = new OperatorState(globalThis.localStorage);
  const ui = makeMasterForm({
    id: 'devMasterOperatorForm',
    label: 'Chave pessoal para modo operador',
    buttonLabel: 'Desbloquear com minha chave',
  });
  pairForm.insertAdjacentElement('afterend', ui.wrapper);
  ui.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    ui.error.textContent = '';
    ui.button.disabled = true;
    const masterKey = ui.input.value;
    try {
      const token = await signedBrowserCredential(masterKey, 'operator');
      await preflight('/v1/operator/messaging/presence', token);
      operatorState.save(token);
      ui.input.value = '';
      location.reload();
    } catch (error) {
      ui.error.textContent = error?.message || String(error);
    } finally {
      ui.button.disabled = false;
    }
  });
}

export function installDevMasterKey({ OperatorState }) {
  installWebMasterPairing();
  installOperatorMasterUnlock(OperatorState);
}

export { signedBrowserCredential };
