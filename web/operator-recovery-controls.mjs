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


function installPCS4Recovery(operatorApi) {
  const summary = document.getElementById('consoleSummary');
  if (!summary || document.getElementById('pcs4RecoveryCard')) return;
  const card = document.createElement('section');
  card.id = 'pcs4RecoveryCard';
  card.className = 'card';
  card.innerHTML = `
    <div class="section-title-row"><h3>Backup e recuperação · PCS.4C</h3><span id="pcs4RecoveryState" class="pill">—</span></div>
    <p class="subtle">Job isolado no Northflank. O Core e o Discord continuam nos serviços atuais. Nenhum dado é restaurado na produção.</p>
    <p id="pcs4RecoveryDetail" class="subtle" aria-live="polite">verificando…</p>
    <div class="form-actions">
      <button type="button" data-pcs4="backup" disabled>Criar backup</button>
      <button type="button" data-pcs4="verify" disabled>Verificar backup</button>
      <button type="button" data-pcs4="restore" disabled>Restaurar banco de teste</button>
      <button type="button" data-pcs4="restart" disabled>Certificar reinicialização</button>
      <button type="button" id="pcs4RecoveryCopy" class="ghost" disabled>Copiar resultados</button>
    </div>
    <pre id="pcs4RecoveryReport" class="subtle" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>`;
  summary.insertAdjacentElement('afterend', card);
  const stateNode = card.querySelector('#pcs4RecoveryState');
  const detail = card.querySelector('#pcs4RecoveryDetail');
  const report = card.querySelector('#pcs4RecoveryReport');
  const copy = card.querySelector('#pcs4RecoveryCopy');
  const buttons = [...card.querySelectorAll('[data-pcs4]')];
  const confirms = {backup:'SNAPSHOT CONSISTENTE', verify:'VOLUME PRIVADO', restore:'DESTINO VAZIO E INDEPENDENTE', restart:'DESTINO ISOLADO'};
  const labels = {idle:'Pronto', ok:'Etapa concluída', running:'Executando', disabled:'Não configurado', blocked:'Bloqueado', error:'Falha'};
  let busy = false;
  let reading = false;
  let latest = {};
  function render(state) {
    stateNode.textContent = labels[state.status] || 'Indisponível';
    detail.textContent = state.status === 'disabled'
      ? 'Configure um Job dedicado e o token de API no Northflank. Não é necessário outro serviço web.'
      : state.status === 'running' ? 'O Job está executando. Pode fechar a página; consulte novamente depois.'
      : state.reason || (state.next ? 'Próxima etapa: ' + state.next : 'Certificação concluída.');
    buttons.forEach(button => { button.disabled = busy || state.next !== button.dataset.pcs4; });
    latest = state.reports || {};
    report.textContent = JSON.stringify(latest, null, 2);
    copy.disabled = !Object.keys(latest).length;
  }
  async function refresh() {
    if (reading || busy || document.getElementById('consoleView')?.classList.contains('hidden')) return;
    reading = true;
    try { render(await operatorApi('/v1/operator/recovery/status')); }
    catch (error) {
      buttons.forEach(button => { button.disabled = true; });
      stateNode.textContent = 'Indisponível'; detail.textContent = humanError(error);
    } finally { reading = false; }
  }
  buttons.forEach(button => button.addEventListener('click', async () => {
    const action = button.dataset.pcs4;
    const confirmation = window.prompt('Confirme esta etapa digitando: ' + confirms[action]);
    if (confirmation !== confirms[action]) return;
    busy = true;
    buttons.forEach(item => { item.disabled = true; });
    try {
      await operatorApi('/v1/operator/recovery/' + action, {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirmation})});
      stateNode.textContent = 'Executando';
    } catch (error) { detail.textContent = humanError(error); }
    finally { busy = false; await refresh(); }
  }));
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(latest, null, 2)); detail.textContent = 'Resultados copiados.'; }
    catch (_) { detail.textContent = 'Selecione o relatório para copiar.'; }
  });
  document.querySelector('[data-surface="consoleView"]')?.addEventListener('click', () => setTimeout(refresh, 0));
  document.getElementById('refreshConsole')?.addEventListener('click', refresh);
  setInterval(refresh, 5000);
  refresh();
}

export function installOperatorRecoveryControls({ operatorKey }) {
  if (typeof document === 'undefined') return;
  const operatorApi = operatorClient(operatorKey);
  installAliasGuard();
  installFamilyRestore(operatorApi);
  installDiscordConsole(operatorApi);
  installPCS4Recovery(operatorApi);
}
