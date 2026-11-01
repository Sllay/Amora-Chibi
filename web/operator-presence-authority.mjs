function byId(id) {
  return document.getElementById(id);
}

function credential(operatorKey) {
  try { return localStorage.getItem(operatorKey) || ''; } catch (_) { return ''; }
}

async function operatorApi(operatorKey, path, init = {}) {
  const token = credential(operatorKey);
  if (!token) throw new Error('modo operador bloqueado');
  const headers = new Headers(init.headers || {});
  headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers, cache: 'no-store' });
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = null; }
  }
  if (!response.ok) throw new Error(payload?.detail || text || `HTTP ${response.status}`);
  return payload;
}

function installField() {
  if (byId('profilePresenceAuthority')) return byId('profilePresenceAuthority');
  const family = byId('profileFamilyAuthority');
  const parent = family?.closest('label');
  if (!parent) return null;

  const label = document.createElement('label');
  label.className = parent.className;
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.id = 'profilePresenceAuthority';
  input.disabled = true;
  const text = document.createElement('span');
  text.textContent = 'Pode controlar presença no Discord/WhatsApp';
  label.append(input, text);
  parent.insertAdjacentElement('afterend', label);

  const hint = document.createElement('small');
  hint.id = 'profilePresenceAuthorityHint';
  hint.className = 'muted';
  hint.textContent = 'Autoridade operacional separada da autoridade familiar.';
  label.insertAdjacentElement('afterend', hint);
  return input;
}

export function installPresenceAuthorityControl({ operatorKey }) {
  if (typeof document === 'undefined') return;
  const field = installField();
  const form = byId('profileForm');
  const entity = byId('profileEntityId');
  if (!field || !form || !entity) return;

  let lastEntityId = '';
  let currentValue = false;
  let refreshGeneration = 0;

  async function refresh() {
    const generation = ++refreshGeneration;
    const entityId = entity.value.trim().toUpperCase();
    const isNew = form.dataset.new === '1';
    lastEntityId = entityId;
    if (!entityId || isNew || !credential(operatorKey)) {
      field.checked = false;
      field.disabled = true;
      currentValue = false;
      return;
    }
    try {
      const payload = await operatorApi(operatorKey, '/v1/operator/people');
      if (generation !== refreshGeneration || entity.value.trim().toUpperCase() !== entityId) return;
      const profile = (payload.profiles || []).find((item) => item.entity_id === entityId);
      currentValue = Boolean(profile?.can_control_platform_presence);
      field.checked = currentValue;
      field.disabled = !profile;
    } catch (_) {
      field.checked = false;
      field.disabled = true;
    }
  }

  field.addEventListener('change', async () => {
    const entityId = entity.value.trim().toUpperCase();
    const desired = field.checked;
    if (!entityId || form.dataset.new === '1') {
      field.checked = currentValue;
      return;
    }
    const action = desired ? 'conceder' : 'revogar';
    if (!confirm(`${action[0].toUpperCase()}${action.slice(1)} permissão para controlar presença da Amora no Discord/WhatsApp?`)) {
      field.checked = currentValue;
      return;
    }
    field.disabled = true;
    try {
      const saved = await operatorApi(
        operatorKey,
        `/v1/operator/messaging/presence-authority/${encodeURIComponent(entityId)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: desired, confirm: true }),
        },
      );
      currentValue = Boolean(saved.can_control_platform_presence);
      field.checked = currentValue;
    } catch (error) {
      field.checked = currentValue;
      alert(error?.message || String(error));
    } finally {
      field.disabled = false;
    }
  });

  byId('peopleList')?.addEventListener('click', () => setTimeout(refresh, 0));
  byId('newPerson')?.addEventListener('click', () => setTimeout(refresh, 0));
  byId('navPeople')?.addEventListener('click', () => setTimeout(refresh, 100));
  form.addEventListener('submit', () => setTimeout(refresh, 250));

  const title = byId('profileEditorTitle');
  if (title && typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      const next = entity.value.trim().toUpperCase();
      if (next !== lastEntityId || form.dataset.new === '1') setTimeout(refresh, 0);
    }).observe(title, { childList: true, subtree: true });
  }
  setTimeout(refresh, 0);
}
