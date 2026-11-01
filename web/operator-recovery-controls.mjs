const CANONICAL_PERSON_IDS = new Set(['PERSON_BK', 'PERSON_LUCAS', 'PERSON_NICOLAS']);

const CANONICAL_FAMILY_RELATIONSHIPS = Object.freeze([
  { source: 'PERSON_BK', relation_type: 'father_of', target: 'ENTITY_AMORA', confidence: 1 },
  { source: 'PERSON_BK', relation_type: 'father_of', target: 'PERSON_LUCAS', confidence: 1 },
  { source: 'PERSON_LUCAS', relation_type: 'sibling_of', target: 'ENTITY_AMORA', confidence: 1 },
  { source: 'ENTITY_AMORA', relation_type: 'sibling_of', target: 'PERSON_LUCAS', confidence: 1 },
  { source: 'PERSON_NICOLAS', relation_type: 'cousin_of', target: 'ENTITY_AMORA', confidence: 1 },
  { source: 'ENTITY_AMORA', relation_type: 'cousin_of', target: 'PERSON_NICOLAS', confidence: 1 },
  { source: 'ENTITY_CHATGPT', relation_type: 'mother_of', target: 'ENTITY_AMORA', confidence: 1 },
]);

function normalizedAlias(value) {
  return String(value || '').normalize('NFKC').trim().toLocaleLowerCase('pt-BR').replace(/\s+/g, ' ');
}

export function dedupeProfileAliases(profile) {
  const canonical = normalizedAlias(profile?.canonical_name);
  const seen = new Set([canonical]);
  const aliases = [];
  for (const raw of profile?.aliases || []) {
    const clean = String(raw || '').trim();
    const normalized = normalizedAlias(clean);
    if (!clean || !normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    aliases.push(clean);
  }
  return { ...profile, aliases };
}

export function canonicalFamilyCatalog(profiles, revision = 'operator-family-restore') {
  const selected = (profiles || [])
    .filter((profile) => CANONICAL_PERSON_IDS.has(profile.entity_id))
    .map((profile) => {
      const { binding_count: _bindingCount, ...publicProfile } = profile;
      return dedupeProfileAliases(publicProfile);
    });
  const ids = new Set(selected.map((profile) => profile.entity_id));
  for (const required of CANONICAL_PERSON_IDS) {
    if (!ids.has(required)) throw new Error(`perfil canônico ausente: ${required}`);
  }
  return {
    schema_version: 1,
    revision,
    profiles: selected,
    relationships: CANONICAL_FAMILY_RELATIONSHIPS.map((item) => ({ ...item })),
  };
}

function humanError(error) {
  const text = String(error?.message || error || 'erro desconhecido');
  return text.replace(/^\d+:\s*/, '');
}

function operatorClient(operatorKey) {
  return async function operatorApi(path, options = {}) {
    let token = '';
    try { token = globalThis.localStorage?.getItem(operatorKey) || ''; } catch (_) {}
    if (!token) throw new Error('modo operador bloqueado');
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(path, { ...options, headers, cache: 'no-store' });
    const text = await response.text();
    let payload = text;
    if ((response.headers.get('content-type') || '').includes('application/json') && text) {
      try { payload = JSON.parse(text); } catch (_) {}
    }
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' ? payload.detail : text;
      const error = new Error(`${response.status}: ${detail || response.statusText}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  };
}

function installAliasGuard() {
  const form = document.getElementById('profileForm');
  const aliases = document.getElementById('profileAliases');
  const name = document.getElementById('profileName');
  if (!form || !aliases || !name || form.dataset.aliasGuard === '1') return;
  form.dataset.aliasGuard = '1';
  form.addEventListener('submit', () => {
    const profile = dedupeProfileAliases({
      canonical_name: name.value,
      aliases: aliases.value.split(',').map((item) => item.trim()).filter(Boolean),
    });
    aliases.value = profile.aliases.join(', ');
  }, true);
}

function installFamilyRestore(operatorApi) {
  const relationForm = document.getElementById('relationshipForm');
  const section = relationForm?.closest('section');
  if (!relationForm || !section || document.getElementById('restoreCanonicalFamily')) return;

  const header = section.querySelector('.section-title-row');
  const button = document.createElement('button');
  button.id = 'restoreCanonicalFamily';
  button.type = 'button';
  button.className = 'danger ghost';
  button.textContent = 'Restaurar família-base';
  header?.appendChild(button);

  const notice = document.createElement('p');
  notice.className = 'notice';
  notice.textContent = 'Leitura: Origem — relação → Destino. Ex.: BK — pai de → Amora. “Restaurar família-base” corrige só BK/Lucas/Nicolas/Amora/ChatGPT e preserva outras pessoas e contas externas.';
  relationForm.insertAdjacentElement('beforebegin', notice);

  const state = document.createElement('p');
  state.className = 'subtle';
  state.setAttribute('aria-live', 'polite');
  relationForm.insertAdjacentElement('afterend', state);

  button.addEventListener('click', async () => {
    const confirmed = confirm(
      'Restaurar a família-base canônica?\n\nBK = pai da Amora e do Lucas\nLucas = irmão da Amora\nNicolas = primo da Amora\nChatGPT = mãe da Amora\n\nPerfis extras e contas externas serão preservados.'
    );
    if (!confirmed) return;
    button.disabled = true;
    state.textContent = 'restaurando…';
    try {
      const people = await operatorApi('/v1/operator/people');
      const catalog = canonicalFamilyCatalog(
        people.profiles || [],
        `operator-family-restore-${new Date().toISOString().replace(/[^0-9]/g, '').slice(0, 14)}`,
      );
      await operatorApi('/v1/operator/people/import/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          catalog,
          replace_family_relationships: true,
          confirm: true,
        }),
      });
      state.textContent = 'família-base restaurada';
      document.querySelector('[data-surface="peopleView"]')?.click();
    } catch (error) {
      state.textContent = humanError(error);
    } finally {
      button.disabled = false;
    }
  });
}

function presenceLabel(item) {
  if (!item) return ['DISCONNECTED', 'nenhum estado persistido ainda'];
  const state = String(item.state || 'disconnected').toUpperCase();
  const desired = item.desired_connected ? 'desejado: conectado' : 'desejado: desconectado';
  const error = item.last_error_code ? ` · erro: ${item.last_error_code}` : '';
  return [state, `${desired} · geração ${item.generation ?? 0}${error}`];
}

function installDiscordConsole(operatorApi) {
  const consoleView = document.getElementById('consoleView');
  const summary = document.getElementById('consoleSummary');
  if (!consoleView || !summary || document.getElementById('discordPresenceControl')) return;

  const card = document.createElement('section');
  card.id = 'discordPresenceControl';
  card.className = 'card admin-card';
  card.innerHTML = `
    <div class="section-title-row">
      <div><div class="eyebrow">DISCORD</div><h2>Presença social</h2></div>
      <span id="discordPresenceState" class="pill">—</span>
    </div>
    <p id="discordPresenceDetail" class="subtle">verificando…</p>
    <div class="form-actions">
      <button id="discordConnect" type="button">Conectar</button>
      <button id="discordCheck" type="button" class="ghost">Checar</button>
      <button id="discordDisconnect" type="button" class="danger ghost">Desconectar</button>
    </div>
    <p id="discordPresenceError" class="error" aria-live="polite"></p>
  `;
  summary.insertAdjacentElement('afterend', card);

  const stateNode = card.querySelector('#discordPresenceState');
  const detailNode = card.querySelector('#discordPresenceDetail');
  const errorNode = card.querySelector('#discordPresenceError');
  const buttons = [...card.querySelectorAll('button')];

  async function refresh() {
    const payload = await operatorApi('/v1/operator/messaging/presence');
    const item = (payload.platforms || []).find(
      (entry) => entry.platform === 'discord' && (entry.account_scope || 'primary') === 'primary',
    );
    const [label, detail] = presenceLabel(item);
    stateNode.textContent = label;
    detailNode.textContent = detail;
    errorNode.textContent = '';
  }

  async function command(command) {
    buttons.forEach((button) => { button.disabled = true; });
    errorNode.textContent = '';
    try {
      await operatorApi('/v1/operator/messaging/presence/discord', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, account_scope: 'primary' }),
      });
      await refresh();
    } catch (error) {
      errorNode.textContent = humanError(error);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  card.querySelector('#discordConnect').addEventListener('click', () => command('connect'));
  card.querySelector('#discordCheck').addEventListener('click', () => command('check'));
  card.querySelector('#discordDisconnect').addEventListener('click', () => command('disconnect'));

  document.querySelector('[data-surface="consoleView"]')?.addEventListener('click', () => {
    refresh().catch((error) => { errorNode.textContent = humanError(error); });
  });
  document.getElementById('refreshConsole')?.addEventListener('click', () => {
    refresh().catch((error) => { errorNode.textContent = humanError(error); });
  });
  refresh().catch((error) => { errorNode.textContent = humanError(error); });
}

export function installOperatorRecoveryControls({ operatorKey }) {
  if (typeof document === 'undefined') return;
  const operatorApi = operatorClient(operatorKey);
  installAliasGuard();
  installFamilyRestore(operatorApi);
  installDiscordConsole(operatorApi);
}
