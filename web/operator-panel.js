import { OperatorState } from "./operator-state.mjs";

const $ = (id) => document.getElementById(id);
const operatorState = new OperatorState(globalThis.localStorage);

const RELATION_LABELS = {
  father_of: "pai de",
  mother_of: "mãe de",
  child_of: "filho(a) de",
  sibling_of: "irmão/irmã de",
  cousin_of: "primo/prima de",
  uncle_of: "tio de",
  aunt_of: "tia de",
  nephew_of: "sobrinho de",
  niece_of: "sobrinha de",
  grandfather_of: "avô de",
  grandmother_of: "avó de",
  grandchild_of: "neto(a) de",
};

const state = {
  token: operatorState.get(),
  surface: "chatView",
  profiles: [],
  entities: [],
  relationships: [],
  relationTypes: [],
  symmetricTypes: new Set(),
  selectedEntityId: null,
  editingBindingRef: null,
  pendingImport: null,
  consoleTimer: null,
};

function operatorHeaders(options = {}) {
  const headers = new Headers(options.headers || {});
  if (!state.token) throw new Error("modo operador bloqueado");
  headers.set("Authorization", `Bearer ${state.token}`);
  return headers;
}

async function operatorApi(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: operatorHeaders(options),
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text || response.statusText;
    try { detail = JSON.parse(text)?.detail || detail; } catch (_) {}
    if (response.status === 401) {
      operatorState.clear();
      state.token = "";
    }
    const error = new Error(`${response.status}: ${detail}`);
    error.status = response.status;
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

function setOperatorError(message = "") {
  $("operatorPairError").textContent = message;
}

function humanError(error) {
  const text = String(error?.message || error || "erro desconhecido");
  return text.replace(/^\d+:\s*/, "");
}

function setActiveTab(surface) {
  document.querySelectorAll(".surface-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.surface === surface);
  });
}

function stopConsoleRefresh() {
  if (state.consoleTimer !== null) {
    clearInterval(state.consoleTimer);
    state.consoleTimer = null;
  }
}

async function showSurface(surface) {
  state.surface = surface;
  setActiveTab(surface);
  stopConsoleRefresh();

  const adminSurface = surface === "peopleView" || surface === "consoleView";
  const hasOperator = Boolean(state.token = operatorState.get());
  $("operatorGate").classList.toggle("hidden", !adminSurface || hasOperator);

  for (const id of ["chatView", "peopleView", "consoleView"]) {
    $(id).classList.add("hidden");
  }
  if (!adminSurface || hasOperator) $(surface).classList.remove("hidden");

  if (surface === "peopleView" && hasOperator) {
    await loadPeople().catch((error) => {
      if (error.status === 401) return showSurface("peopleView");
      $("peopleList").textContent = humanError(error);
    });
  }
  if (surface === "consoleView" && hasOperator) {
    await loadConsole().catch((error) => {
      if (error.status === 401) return showSurface("consoleView");
      $("consoleSummary").textContent = humanError(error);
    });
    state.consoleTimer = setInterval(() => {
      if (state.surface === "consoleView" && operatorState.get()) loadConsole().catch(() => {});
    }, 10000);
  }
}

document.querySelectorAll(".surface-tab").forEach((button) => {
  button.addEventListener("click", () => showSurface(button.dataset.surface));
});

$("operatorPairForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  setOperatorError();
  const code = $("operatorPairCode").value.trim();
  if (!/^\d{6}$/.test(code)) {
    setOperatorError("Digite o código de 6 dígitos.");
    return;
  }
  try {
    const response = await fetch("/v1/pairing/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, client: "operator" }),
      cache: "no-store",
    });
    const text = await response.text();
    if (!response.ok) {
      let detail = text;
      try { detail = JSON.parse(text)?.detail || detail; } catch (_) {}
      throw new Error(detail || "código inválido");
    }
    const payload = JSON.parse(text);
    state.token = operatorState.save(payload.token);
    $("operatorPairCode").value = "";
    await showSurface(state.surface);
  } catch (error) {
    setOperatorError(humanError(error));
  }
});

function lockOperator() {
  operatorState.clear();
  state.token = "";
  stopConsoleRefresh();
  showSurface(state.surface);
}
$("lockOperatorPeople").addEventListener("click", lockOperator);
$("lockOperatorConsole").addEventListener("click", lockOperator);

function selectedProfile() {
  return state.profiles.find((item) => item.entity_id === state.selectedEntityId) || null;
}

function profileSubtitle(profile) {
  const related = state.relationships.filter((item) => item.source_id === profile.entity_id || item.target_id === profile.entity_id);
  if (!related.length) return "sem parentesco cadastrado";
  const first = related[0];
  if (first.source_id === profile.entity_id) {
    return `${RELATION_LABELS[first.relation_type] || first.relation_type} ${first.target_name}`;
  }
  return `${first.source_name} · ${RELATION_LABELS[first.relation_type] || first.relation_type}`;
}

function renderPeopleList() {
  const list = $("peopleList");
  list.replaceChildren();
  $("peopleCount").textContent = String(state.profiles.length);
  for (const profile of state.profiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `person-card ${profile.entity_id === state.selectedEntityId ? "selected" : ""}`;
    const name = document.createElement("strong");
    name.textContent = profile.canonical_name;
    const subtitle = document.createElement("span");
    subtitle.textContent = profileSubtitle(profile);
    const count = document.createElement("small");
    count.textContent = `${profile.binding_count || 0} conta(s)`;
    button.append(name, subtitle, count);
    button.addEventListener("click", () => selectPerson(profile.entity_id));
    list.appendChild(button);
  }
}

function entityOption(entity, selected = false) {
  const option = document.createElement("option");
  option.value = entity.id;
  option.textContent = `${entity.name} · ${entity.id}`;
  option.selected = selected;
  return option;
}

function refillRelationshipSelects() {
  const source = $("relationshipSource");
  const target = $("relationshipTarget");
  const relation = $("relationshipType");
  const previousSource = source.value || state.selectedEntityId || "PERSON_BK";
  const previousTarget = target.value || "ENTITY_AMORA";
  const previousRelation = relation.value || "sibling_of";
  source.replaceChildren();
  target.replaceChildren();
  relation.replaceChildren();
  for (const entity of state.entities) {
    source.appendChild(entityOption(entity, entity.id === previousSource));
    target.appendChild(entityOption(entity, entity.id === previousTarget));
  }
  for (const type of state.relationTypes) {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = `${RELATION_LABELS[type] || type} · ${type}`;
    option.selected = type === previousRelation;
    relation.appendChild(option);
  }
}

function renderRelationships() {
  refillRelationshipSelects();
  const list = $("relationshipList");
  list.replaceChildren();
  if (!state.relationships.length) {
    list.textContent = "Nenhuma relação estrutural cadastrada.";
    return;
  }
  for (const item of state.relationships) {
    const row = document.createElement("div");
    row.className = "relation-row";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${item.source_name} ${RELATION_LABELS[item.relation_type] || item.relation_type} ${item.target_name}`;
    const meta = document.createElement("small");
    meta.textContent = `${item.relation_type} · confiança ${Number(item.confidence).toFixed(2)} · ${item.source_of_truth}`;
    text.append(title, meta);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger ghost compact-button";
    remove.textContent = "Remover";
    remove.addEventListener("click", () => removeRelationship(item));
    row.append(text, remove);
    list.appendChild(row);
  }
}

function clearProfileEditor() {
  state.selectedEntityId = null;
  $("profileForm").classList.add("hidden");
  $("profileEmpty").classList.remove("hidden");
  $("profileEditorTitle").textContent = "Nova pessoa";
  $("addBinding").disabled = true;
  $("bindingsList").replaceChildren();
  renderPeopleList();
}

function fillProfileForm(profile, { isNew = false } = {}) {
  $("profileEmpty").classList.add("hidden");
  $("profileForm").classList.remove("hidden");
  $("profileEditorTitle").textContent = isNew ? "Nova pessoa" : profile.canonical_name;
  $("profileEntityId").value = profile.entity_id || "PERSON_";
  $("profileEntityId").readOnly = !isNew;
  $("profileName").value = profile.canonical_name || "";
  $("profileAliases").value = [...(profile.aliases || [])].join(", ");
  $("profileBirthDate").value = profile.birth_date || "";
  $("profileAge").value = profile.age_years ?? "";
  $("profileDescription").value = profile.short_description || "";
  $("profileFamilyAuthority").checked = Boolean(profile.can_define_amora_family);
  $("profileForm").dataset.new = isNew ? "1" : "0";
  $("profileSaveState").textContent = "";
  $("addBinding").disabled = isNew;
}

async function selectPerson(entityId) {
  state.selectedEntityId = entityId;
  renderPeopleList();
  const profile = selectedProfile();
  if (!profile) return clearProfileEditor();
  fillProfileForm(profile);
  refillRelationshipSelects();
  $("relationshipSource").value = entityId;
  await loadBindings(entityId);
}

$("newPerson").addEventListener("click", () => {
  state.selectedEntityId = null;
  renderPeopleList();
  fillProfileForm({
    entity_id: "PERSON_",
    canonical_name: "",
    aliases: [],
    birth_date: null,
    age_years: null,
    short_description: null,
    can_define_amora_family: false,
  }, { isNew: true });
  $("bindingsList").replaceChildren();
});

$("profileBirthDate").addEventListener("change", () => {
  if ($("profileBirthDate").value) $("profileAge").value = "";
});
$("profileAge").addEventListener("input", () => {
  if ($("profileAge").value) $("profileBirthDate").value = "";
});

$("profileName").addEventListener("input", () => {
  if ($("profileForm").dataset.new !== "1") return;
  const slug = $("profileName").value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (slug) $("profileEntityId").value = `PERSON_${slug}`;
});

$("profileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const entityId = $("profileEntityId").value.trim().toUpperCase();
  const ageText = $("profileAge").value.trim();
  const aliases = $("profileAliases").value.split(",").map((item) => item.trim()).filter(Boolean);
  const profile = {
    entity_id: entityId,
    canonical_name: $("profileName").value.trim(),
    aliases,
    birth_date: $("profileBirthDate").value || null,
    age_years: ageText ? Number(ageText) : null,
    short_description: $("profileDescription").value.trim() || null,
    can_define_amora_family: $("profileFamilyAuthority").checked,
  };
  const old = state.profiles.find((item) => item.entity_id === entityId);
  const authorityChanged = !old ? profile.can_define_amora_family : old.can_define_amora_family !== profile.can_define_amora_family;
  if (authorityChanged && !confirm("Alterar autoridade familiar muda quem pode oficializar parentesco estrutural da Amora. Confirmar?")) return;
  $("profileSaveState").textContent = "salvando…";
  try {
    await operatorApi(`/v1/operator/people/${encodeURIComponent(entityId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, confirm_family_authority_change: authorityChanged }),
    });
    state.selectedEntityId = entityId;
    await loadPeople();
    $("profileSaveState").textContent = "salvo";
  } catch (error) {
    $("profileSaveState").textContent = humanError(error);
  }
});

$("relationshipForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    source_id: $("relationshipSource").value,
    relation_type: $("relationshipType").value,
    target_id: $("relationshipTarget").value,
    confidence: Number($("relationshipConfidence").value || 1),
    mirror: $("relationshipMirror").checked,
    confirm_revision: false,
  };
  const current = state.relationships.find((item) => item.source_id === payload.source_id && item.relation_type === payload.relation_type && item.target_id === payload.target_id);
  if (current && Number(current.confidence) !== payload.confidence) {
    if (!confirm("Essa relação já existe com outra confiança. Criar uma revisão no histórico?")) return;
    payload.confirm_revision = true;
  }
  try {
    await operatorApi("/v1/operator/relationships", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadRelationships();
    renderRelationships();
    renderPeopleList();
  } catch (error) {
    alert(humanError(error));
  }
});

async function removeRelationship(item) {
  if (!confirm(`Remover ${item.source_name} ${RELATION_LABELS[item.relation_type] || item.relation_type} ${item.target_name}? O histórico temporal será preservado.`)) return;
  const query = new URLSearchParams({
    source_id: item.source_id,
    relation_type: item.relation_type,
    target_id: item.target_id,
    mirror: String(state.symmetricTypes.has(item.relation_type)),
    confirm: "true",
  });
  try {
    await operatorApi(`/v1/operator/relationships?${query}`, { method: "DELETE" });
    await loadRelationships();
    renderRelationships();
    renderPeopleList();
  } catch (error) {
    alert(humanError(error));
  }
}

async function loadPeople() {
  const [people, relationships] = await Promise.all([
    operatorApi("/v1/operator/people"),
    operatorApi("/v1/operator/relationships"),
  ]);
  state.profiles = people.profiles || [];
  state.entities = people.entities || [];
  state.relationTypes = people.family_relation_types || [];
  state.symmetricTypes = new Set(people.symmetric_relation_types || []);
  state.relationships = relationships.relationships || [];
  if (state.selectedEntityId && !state.profiles.some((item) => item.entity_id === state.selectedEntityId)) state.selectedEntityId = null;
  renderPeopleList();
  renderRelationships();
  if (!state.selectedEntityId && state.profiles.length) state.selectedEntityId = state.profiles[0].entity_id;
  if (state.selectedEntityId) await selectPerson(state.selectedEntityId);
}

async function loadRelationships() {
  const payload = await operatorApi("/v1/operator/relationships");
  state.relationships = payload.relationships || [];
}

async function loadBindings(entityId) {
  const list = $("bindingsList");
  list.textContent = "carregando…";
  try {
    const payload = await operatorApi(`/v1/operator/people/${encodeURIComponent(entityId)}/bindings`);
    renderBindings(payload.bindings || []);
  } catch (error) {
    list.textContent = humanError(error);
  }
}

function renderBindings(bindings) {
  const list = $("bindingsList");
  list.replaceChildren();
  if (!bindings.length) {
    list.textContent = "Nenhuma conta externa cadastrada.";
    return;
  }
  for (const item of bindings) {
    const row = document.createElement("div");
    row.className = "binding-row";
    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${item.platform} · ${item.masked_external_actor_id}`;
    const meta = document.createElement("small");
    meta.textContent = `${item.identifier_type} · ${item.account_scope} · ${item.enabled ? "ativo" : "desativado"}`;
    info.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    const reveal = document.createElement("button");
    reveal.type = "button";
    reveal.className = "ghost compact-button";
    reveal.textContent = "Revelar";
    reveal.addEventListener("click", async () => {
      try {
        const raw = await operatorApi(`/v1/operator/people/${encodeURIComponent(item.entity_id)}/bindings/${item.ref}`);
        title.textContent = `${item.platform} · ${raw.external_actor_id}`;
        reveal.textContent = "Ocultar";
        reveal.onclick = () => {
          title.textContent = `${item.platform} · ${item.masked_external_actor_id}`;
          reveal.textContent = "Revelar";
          renderBindings(bindings);
        };
      } catch (error) { alert(humanError(error)); }
    });
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "ghost compact-button";
    edit.textContent = "Editar";
    edit.addEventListener("click", () => openBindingDialog(item));
    const disable = document.createElement("button");
    disable.type = "button";
    disable.className = "danger ghost compact-button";
    disable.textContent = item.enabled ? "Desativar" : "Desativado";
    disable.disabled = !item.enabled;
    disable.addEventListener("click", async () => {
      if (!confirm("Desativar essa identidade externa?")) return;
      try {
        await operatorApi(`/v1/operator/people/${encodeURIComponent(item.entity_id)}/bindings/${item.ref}?confirm=true`, { method: "DELETE" });
        await loadBindings(item.entity_id);
      } catch (error) { alert(humanError(error)); }
    });
    actions.append(reveal, edit, disable);
    row.append(info, actions);
    list.appendChild(row);
  }
}

$("bindingPlatform").addEventListener("change", () => {
  const defaults = { whatsapp: "phone_e164", discord: "discord_user_id", web: "account_id" };
  $("bindingIdentifierType").value = defaults[$("bindingPlatform").value] || "external_id";
});

$("addBinding").addEventListener("click", () => openBindingDialog(null));
$("closeBindingDialog").addEventListener("click", () => $("bindingDialog").close());

async function openBindingDialog(summary) {
  const profile = selectedProfile();
  if (!profile) return;
  state.editingBindingRef = summary?.ref || null;
  $("bindingTitle").textContent = summary ? "Editar conta externa" : `Nova conta · ${profile.canonical_name}`;
  $("bindingError").textContent = "";
  let raw = null;
  if (summary) {
    try {
      raw = await operatorApi(`/v1/operator/people/${encodeURIComponent(profile.entity_id)}/bindings/${summary.ref}`);
    } catch (error) {
      alert(humanError(error));
      return;
    }
  }
  $("bindingPlatform").value = raw?.platform || "whatsapp";
  $("bindingIdentifierType").value = raw?.identifier_type || "phone_e164";
  $("bindingExternalId").value = raw?.external_actor_id || "";
  $("bindingScope").value = raw?.account_scope || "primary";
  $("bindingTrust").value = raw?.trust || "operator_verified";
  $("bindingConfidence").value = raw?.confidence ?? 1;
  $("bindingEnabled").checked = raw?.enabled ?? true;
  $("bindingDialog").showModal();
}

$("bindingForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const profile = selectedProfile();
  if (!profile) return;
  const payload = {
    platform: $("bindingPlatform").value,
    identifier_type: $("bindingIdentifierType").value.trim(),
    external_actor_id: $("bindingExternalId").value.trim(),
    entity_id: profile.entity_id,
    account_scope: $("bindingScope").value.trim() || "primary",
    trust: $("bindingTrust").value,
    confidence: Number($("bindingConfidence").value || 1),
    enabled: $("bindingEnabled").checked,
  };
  const editing = state.editingBindingRef;
  const path = editing
    ? `/v1/operator/people/${encodeURIComponent(profile.entity_id)}/bindings/${editing}`
    : `/v1/operator/people/${encodeURIComponent(profile.entity_id)}/bindings`;
  try {
    await operatorApi(path, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("bindingDialog").close();
    state.editingBindingRef = null;
    await loadPeople();
  } catch (error) {
    $("bindingError").textContent = humanError(error);
  }
});

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("exportPeople").addEventListener("click", async () => {
  try { downloadJson("amora-people-profiles.json", await operatorApi("/v1/operator/people/export")); }
  catch (error) { alert(humanError(error)); }
});

$("exportBindings").addEventListener("click", async () => {
  if (!confirm("Esse arquivo contém números/IDs privados sem máscara. Exportar mesmo assim?")) return;
  try { downloadJson("amora-private-identity-bindings.json", await operatorApi("/v1/operator/bindings/export")); }
  catch (error) { alert(humanError(error)); }
});

$("importPeople").addEventListener("click", () => $("peopleImportFile").click());
$("importBindings").addEventListener("click", () => $("bindingsImportFile").click());

async function readJsonFile(input) {
  const file = input.files?.[0];
  input.value = "";
  if (!file) return null;
  if (file.size > 256 * 1024) throw new Error("JSON maior que 256 KiB");
  return JSON.parse(await file.text());
}

async function previewImport(kind, payload) {
  const isPeople = kind === "people";
  const request = isPeople
    ? { catalog: payload, replace_family_relationships: true, confirm: false }
    : { payload, replace: false, confirm: false };
  const endpoint = isPeople ? "/v1/operator/people/import/preview" : "/v1/operator/bindings/import/preview";
  const preview = await operatorApi(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  state.pendingImport = { kind, payload, request };
  $("jsonPreviewTitle").textContent = isPeople ? "Prévia · perfis e família" : "Prévia · IDs privados";
  $("jsonPreview").textContent = JSON.stringify(preview, null, 2);
  $("jsonPreviewDialog").showModal();
}

$("peopleImportFile").addEventListener("change", async () => {
  try {
    const payload = await readJsonFile($("peopleImportFile"));
    if (payload) await previewImport("people", payload);
  } catch (error) { alert(humanError(error)); }
});

$("bindingsImportFile").addEventListener("change", async () => {
  try {
    const payload = await readJsonFile($("bindingsImportFile"));
    if (payload) await previewImport("bindings", payload);
  } catch (error) { alert(humanError(error)); }
});

$("cancelJsonImport").addEventListener("click", () => {
  state.pendingImport = null;
  $("jsonPreviewDialog").close();
});

$("confirmJsonImport").addEventListener("click", async () => {
  if (!state.pendingImport) return;
  const { kind, request } = state.pendingImport;
  const apply = { ...request, confirm: true };
  const endpoint = kind === "people" ? "/v1/operator/people/import/apply" : "/v1/operator/bindings/import/apply";
  try {
    await operatorApi(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(apply),
    });
    state.pendingImport = null;
    $("jsonPreviewDialog").close();
    await loadPeople();
  } catch (error) {
    alert(humanError(error));
  }
});

function scalar(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Number.isFinite(value) ? String(Math.round(value * 100) / 100) : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function summaryCard(label, value, detail = "") {
  const card = document.createElement("section");
  card.className = "card console-metric";
  const eyebrow = document.createElement("div");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = scalar(value);
  const small = document.createElement("small");
  small.textContent = detail;
  card.append(eyebrow, strong, small);
  return card;
}

function renderSimpleList(container, items, formatter) {
  container.replaceChildren();
  if (!items?.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Nada para mostrar.";
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "console-row";
    const [title, detail] = formatter(item);
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = detail;
    row.append(strong, small);
    container.appendChild(row);
  }
}

async function loadConsole() {
  const snapshot = await operatorApi("/v1/operations/state");
  const summary = $("consoleSummary");
  summary.replaceChildren(
    summaryCard("CORE", snapshot.health?.status || snapshot.modules?.core, snapshot.modules?.storage || ""),
    summaryCard("TAREFA", snapshot.task, snapshot.dialogue_act ? `ato: ${snapshot.dialogue_act}` : ""),
    summaryCard("PROVIDER", snapshot.provider || "—", snapshot.model || ""),
    summaryCard("LATÊNCIA", snapshot.latency_ms == null ? "—" : `${Math.round(snapshot.latency_ms)} ms`, `fila: ${snapshot.speech?.pending ?? 0}`),
  );
  renderSimpleList($("consoleProviders"), snapshot.providers || [], (item) => [
    `${item.provider} · ${item.available ? "ON" : "OFF"}`,
    item.model || "",
  ]);
  const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : Object.entries(snapshot.jobs || {}).map(([name, value]) => ({ name, value }));
  renderSimpleList($("consoleJobs"), jobs, (item) => [
    item.name || item.id || item.key || "job",
    typeof item.value === "object" ? JSON.stringify(item.value) : scalar(item.value ?? item.status ?? item.run_at),
  ]);
  renderSimpleList($("consoleErrors"), snapshot.errors || [], (item) => [
    `${item.module} · ${item.error_code}`,
    `${item.occurrence_count} ocorrência(s) · ${item.last_seen}`,
  ]);
  $("consoleRaw").textContent = JSON.stringify(snapshot, null, 2);
}

$("refreshConsole").addEventListener("click", () => loadConsole().catch((error) => alert(humanError(error))));

// Chat remains the default surface and does not need operator credentials.
showSurface("chatView");
