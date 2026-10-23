// Amora Avatar Studio / Runtime V0.9
// @ts-check
import { AvatarRenderer, clamp, humanoidTemplate, standardIK } from './engine.js';

const qs = new URLSearchParams(location.search);
const runtimeMode = qs.get('mode') === 'runtime';
document.body.classList.toggle('runtime', runtimeMode);

const canvas = /** @type {HTMLCanvasElement} */ (document.querySelector('#stage'));
const renderer = new AvatarRenderer(canvas);
renderer.realtimeAnimation = runtimeMode;
renderer.proceduralEnabled = runtimeMode;

const pairModal = document.querySelector('#pairModal');
const pairCode = /** @type {HTMLInputElement} */ (document.querySelector('#pairCode'));
const pairBtn = document.querySelector('#pairBtn');
const connectionPill = document.querySelector('#connectionPill');
const sheet = document.querySelector('#sheet');
const sheetBody = document.querySelector('#sheetBody');
const sheetTitle = document.querySelector('#sheetTitle');
const desktopInspector = document.querySelector('#desktopInspector');
const zoomLabel = document.querySelector('#zoomLabel');
const toastEl = document.querySelector('#toast');
const assetInput = /** @type {HTMLInputElement} */ (document.querySelector('#assetInput'));

localStorage.removeItem('amora.avatar.token');
let token = sessionStorage.getItem('amora.avatar.token') || '';
let project = null;
let assets = new Map();
let selectedPartId = null;
let selectedBoneId = null;
let selectedIKId = null;
let activePanel = 'parts';
let activeTool = 'select';
let dirty = false;
let saveTimer = 0;
let playing = false;
let lastFrame = performance.now();
let history = [];
let historyIndex = -1;
const pointers = new Map();
let gesture = null;
let runtimePoll = 0;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function idSafe(value) {
  const clean = String(value).normalize('NFKD').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 72);
  return clean || `asset_${Date.now()}`;
}
function normalizedName(value) {
  return String(value).normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
}
function uniqueId(base, list) {
  const used = new Set(list.map(item => item.id));
  if (!used.has(base)) return base;
  let i = 2; while (used.has(`${base}_${i}`)) i += 1;
  return `${base}_${i}`;
}
function selectedPart() { return project?.parts.find(item => item.id === selectedPartId) || null; }
function selectedBone() { return project?.bones.find(item => item.id === selectedBoneId) || null; }
function selectedIK() { return project?.ik_chains.find(item => item.id === selectedIKId) || null; }
function activeClip() { return project?.animations.find(item => item.id === renderer.previewClipId) || null; }
function selectedPhysics() { return project?.physics.find(item => item.target_part_id === selectedPartId) || null; }

function toast(text) {
  toastEl.textContent = text; toastEl.classList.add('show');
  clearTimeout(toastEl._timer); toastEl._timer = setTimeout(() => toastEl.classList.remove('show'), 1800);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    token = ''; sessionStorage.removeItem('amora.avatar.token'); showPair(); throw new Error('pairing required');
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response;
}

function showPair() { pairModal.classList.remove('hidden'); connectionPill.textContent = 'parear'; connectionPill.classList.remove('ok'); }
function hidePair() { pairModal.classList.add('hidden'); }

pairBtn?.addEventListener('click', async () => {
  const code = pairCode.value.trim();
  if (!/^\d{6}$/.test(code)) return toast('Código precisa ter 6 dígitos');
  try {
    const response = await fetch('/v1/pairing/redeem', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code,client:"avatar"}) });
    if (!response.ok) throw new Error('Código inválido ou expirado');
    const data = await response.json(); token = data.token; sessionStorage.setItem('amora.avatar.token', token);
    hidePair(); pairCode.value = ''; await bootstrap();
  } catch (error) { toast(error.message || 'Falha ao parear'); }
});

async function loadImageAsset(assetId) {
  const response = await api(`/v1/avatar/assets/${encodeURIComponent(assetId)}`); const blob = await response.blob();
  try { return await createImageBitmap(blob); }
  catch (_) {
    const url = URL.createObjectURL(blob); const image = new Image(); image.src = url; await image.decode(); URL.revokeObjectURL(url); return image;
  }
}
async function loadAssets() {
  const next = new Map();
  await Promise.all((project.assets || []).map(async ref => {
    try { next.set(ref.id, await loadImageAsset(ref.id)); } catch (error) { console.warn('Asset não carregou', ref.id, error); }
  }));
  assets = next; renderer.setAssets(assets);
}

function recordHistory() {
  if (!project || runtimeMode) return;
  const snapshot = JSON.stringify(project);
  if (history[historyIndex] === snapshot) return;
  history = history.slice(0, historyIndex + 1); history.push(snapshot);
  if (history.length > 40) history.shift();
  historyIndex = history.length - 1;
}
function mutate(fn, { history: capture = true, panel = true } = {}) {
  if (!project) return;
  fn(); renderer.setProject(project); dirty = true;
  if (capture) recordHistory();
  scheduleSave(); if (panel) renderPanel();
}
function scheduleSave() {
  if (runtimeMode) return;
  clearTimeout(saveTimer); saveTimer = setTimeout(() => saveProject(false), 850);
}
async function saveProject(notify = true) {
  if (!project || runtimeMode) return;
  clearTimeout(saveTimer);
  try {
    project = await api('/v1/avatar/project', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(project) });
    renderer.setProject(project); dirty = false; connectionPill.textContent = 'salvo'; connectionPill.classList.add('ok');
    if (notify) toast('Projeto salvo no Core');
  } catch (error) {
    connectionPill.textContent = 'erro ao salvar'; connectionPill.classList.remove('ok'); if (notify) toast(`Salvar: ${error.message}`);
  }
}
function restoreHistory(delta) {
  const next = historyIndex + delta; if (next < 0 || next >= history.length) return;
  historyIndex = next; project = JSON.parse(history[historyIndex]); renderer.setProject(project); dirty = true; scheduleSave(); renderPanel();
}

document.querySelector('#undoBtn')?.addEventListener('click', () => restoreHistory(-1));
document.querySelector('#redoBtn')?.addEventListener('click', () => restoreHistory(1));
document.querySelector('#saveBtn')?.addEventListener('click', () => saveProject(true));
document.querySelector('#fitBtn')?.addEventListener('click', () => renderer.fit());
document.querySelector('#gridBtn')?.addEventListener('click', () => { renderer.showGrid = !renderer.showGrid; });
document.querySelector('#sheetClose')?.addEventListener('click', () => sheet.classList.remove('open'));

for (const button of document.querySelectorAll('[data-tool]')) {
  button.addEventListener('click', () => {
    activeTool = button.dataset.tool;
    document.querySelectorAll('[data-tool]').forEach(item => item.classList.toggle('active', item === button));
    renderer.showRig = ['bone','ik'].includes(activeTool); renderer.showIK = activeTool === 'ik';
    if (activeTool === 'bone' || activeTool === 'ik') openPanel('rig');
  });
}
for (const button of document.querySelectorAll('[data-panel]')) button.addEventListener('click', () => openPanel(button.dataset.panel));

function openPanel(name) {
  activePanel = name || 'parts';
  document.querySelectorAll('.bottom-nav [data-panel]').forEach(item => item.classList.toggle('active', item.dataset.panel === activePanel));
  sheet.classList.add('open');
  if (activePanel === 'expressions') renderer.expressionOverrideId = project?.metadata?.editor_expression || 'neutral';
  renderPanel();
}
function panelName(name) {
  return ({parts:'Peças',layers:'Layers',rig:'Rig + IK',colors:'Customização',expressions:'Expressões',animate:'Timeline',project:'Projeto'})[name] || 'Studio';
}
function renderPanel() {
  if (!project || runtimeMode) return;
  const html = panelHtml(activePanel); sheetTitle.textContent = panelName(activePanel); sheetBody.innerHTML = html;
  desktopInspector.innerHTML = `<div style="padding:12px"><h3>${esc(panelName(activePanel))}</h3>${html}</div>`;
  bindPanel(sheetBody); bindPanel(desktopInspector);
}
function panelHtml(name) {
  if (name === 'parts' || name === 'layers') return partsPanel();
  if (name === 'rig') return rigPanel();
  if (name === 'colors') return colorsPanel();
  if (name === 'expressions') return expressionsPanel();
  if (name === 'animate') return animatePanel();
  return projectPanel();
}

function textRow(label,id,value) { return `<div class="row"><label>${esc(label)}</label><input id="${id}" type="text" value="${esc(value)}"></div>`; }
function numberRow(label,id,value,step) { return `<div class="row"><label>${esc(label)}</label><input id="${id}" type="number" step="${step}" value="${esc(value)}"></div>`; }

function partsPanel() {
  const part = selectedPart(); const ordered = [...project.parts].sort((a,b) => b.layer - a.layer);
  let html = `<div class="section"><div class="row"><button data-act="import-assets" class="primary">＋ Importar PNGs</button><span class="badge">${project.parts.length} peças</span></div><div class="item-list">`;
  for (const item of ordered) {
    html += `<div class="item ${item.id===selectedPartId?'selected':''}" data-select-part="${esc(item.id)}"><div class="item-main"><strong>${esc(item.name)}</strong><small>layer ${item.layer} · ${esc(item.bone_id || 'sem bone')} · ${esc(item.tint_slot || 'cor original')}</small></div><div class="item-actions"><button data-layer-up="${esc(item.id)}">↑</button><button data-layer-down="${esc(item.id)}">↓</button></div></div>`;
  }
  html += `</div></div>`;
  if (!part) return html + `<div class="section"><small>Toque numa peça no canvas ou na lista para editar.</small></div>`;
  const boneOptions = [`<option value="">sem bone</option>`, ...project.bones.map(b => `<option value="${esc(b.id)}" ${b.id===part.bone_id?'selected':''}>${esc(b.name)}</option>`)].join('');
  const tintOptions = [`<option value="">cor original</option>`, ...project.color_slots.map(s => `<option value="${esc(s.id)}" ${s.id===part.tint_slot?'selected':''}>${esc(s.name)}</option>`)].join('');
  const physics = selectedPhysics();
  html += `<div class="section"><div class="section-title">${esc(part.name)}</div>
    ${textRow('Nome','part-name',part.name)}${numberRow('Layer','part-layer',part.layer,1)}
    <div class="row"><label>Bone</label><select id="part-bone">${boneOptions}</select></div>
    <div class="row"><label>Cor</label><select id="part-tint">${tintOptions}</select></div>
    ${numberRow('X','part-x',part.transform.x,.5)}${numberRow('Y','part-y',part.transform.y,.5)}${numberRow('Rot °','part-rot',(part.transform.rotation*180/Math.PI).toFixed(1),1)}
    ${numberRow('Escala X','part-sx',part.transform.scale_x,.01)}${numberRow('Escala Y','part-sy',part.transform.scale_y,.01)}${numberRow('Pivô X','part-px',part.transform.pivot_x,.01)}${numberRow('Pivô Y','part-py',part.transform.pivot_y,.01)}
    ${textRow('Tags','part-tags',(part.tags||[]).join(', '))}
    <small>Tags úteis: body, head, blush, eye:pupil, eyelash:top, eyelash:bottom, mouth:neutral, mouth:talk, brow:neutral.</small>
    <div class="row"><button data-act="toggle-part">${part.visible?'Ocultar':'Mostrar'}</button><button data-act="delete-part">Excluir peça</button></div>
  </div>`;
  html += `<div class="section"><div class="section-title">Física secundária</div>`;
  if (!physics) html += `<button data-act="enable-physics">Ativar spring nesta peça</button>`;
  else html += `${numberRow('Stiffness','physics-stiffness',physics.stiffness,.01)}${numberRow('Damping','physics-damping',physics.damping,.01)}${numberRow('Gravity','physics-gravity',physics.gravity,.05)}${numberRow('Limite °','physics-angle',(physics.max_angle*180/Math.PI).toFixed(1),1)}<button data-act="disable-physics">Remover física</button>`;
  html += `</div>`;
  return html;
}

function rigPanel() {
  const bone = selectedBone(); const chain = selectedIK();
  let html = `<div class="section"><div class="row"><button data-act="humanoid" class="primary">Gerar rig humanoide</button><button data-act="standard-ik">Criar IK padrão</button></div><small>O template cria a hierarchy; você só ajusta as articulações no desenho.</small></div>`;
  html += `<div class="section"><div class="section-title">Bones</div><div class="item-list">`;
  for (const item of project.bones) html += `<div class="item ${item.id===selectedBoneId?'selected':''}" data-select-bone="${esc(item.id)}"><div class="item-main"><strong>${esc(item.name)}</strong><small>${esc(item.parent_id || 'root')} · ${(item.rotation*180/Math.PI).toFixed(0)}°</small></div></div>`;
  html += `</div></div>`;
  if (bone) {
    const parents = [`<option value="">root</option>`, ...project.bones.filter(x=>x.id!==bone.id).map(x=>`<option value="${esc(x.id)}" ${x.id===bone.parent_id?'selected':''}>${esc(x.name)}</option>`)].join('');
    html += `<div class="section"><div class="section-title">Bone: ${esc(bone.name)}</div>${textRow('Nome','bone-name',bone.name)}<div class="row"><label>Parent</label><select id="bone-parent">${parents}</select></div>${numberRow('X','bone-x',bone.x,.5)}${numberRow('Y','bone-y',bone.y,.5)}${numberRow('Rot °','bone-rot',(bone.rotation*180/Math.PI).toFixed(1),1)}${numberRow('Compr.','bone-length',bone.length,.5)}</div>`;
  }
  html += `<div class="section"><div class="section-title">IK Chains</div><div class="item-list">`;
  for (const item of project.ik_chains) html += `<div class="item ${item.id===selectedIKId?'selected':''}" data-select-ik="${esc(item.id)}"><div class="item-main"><strong>${esc(item.name)}</strong><small>${esc(item.root_bone_id)} → ${esc(item.joint_bone_id)}</small></div></div>`;
  html += `</div>`;
  if (chain) html += `<div style="margin-top:10px">${numberRow('Target X','ik-x',chain.target.x,.5)}${numberRow('Target Y','ik-y',chain.target.y,.5)}<div class="row"><label>Dobra</label><select id="ik-bend"><option value="positive" ${chain.bend==='positive'?'selected':''}>positive</option><option value="negative" ${chain.bend==='negative'?'selected':''}>negative</option></select></div></div>`;
  return html + `</div>`;
}

function colorsPanel() {
  return `<div class="section"><div class="section-title">Slots de cor</div>${project.color_slots.map(slot => `<div class="row"><label>${esc(slot.name)}</label><span class="color-chip"><input type="color" value="${esc(slot.value)}" data-color="${esc(slot.id)}"></span><code>${esc(slot.value)}</code></div>`).join('')}<div class="row"><button data-act="add-color">＋ slot</button></div></div>`;
}

function expressionsPanel() {
  const selected = project.metadata.editor_expression || 'neutral'; const expression = project.expressions.find(x=>x.id===selected) || project.expressions[0];
  if (!expression) return `<button data-act="add-expression">Criar expressão</button>`;
  renderer.expressionOverrideId = expression.id;
  const expressionOptions = project.expressions.map(item=>`<option value="${esc(item.id)}" ${item.id===expression.id?'selected':''}>${esc(item.name)}</option>`).join('');
  const mouthOptions = [`<option value="">automático por tags</option>`, ...project.parts.map(p=>`<option value="${esc(p.id)}" ${p.id===expression.mouth_part_id?'selected':''}>${esc(p.name)}</option>`)].join('');
  const browOptions = [`<option value="">automático</option>`, ...project.parts.map(p=>`<option value="${esc(p.id)}" ${p.id===expression.brow_part_id?'selected':''}>${esc(p.name)}</option>`)].join('');
  let html = `<div class="section"><div class="row"><label>Expressão</label><select id="expr-select">${expressionOptions}</select><button data-act="add-expression">＋</button></div>${textRow('Nome','expr-name',expression.name)}${numberRow('Blush','expr-blush',expression.blush_opacity ?? 0,.05)}<div class="row"><label>Boca</label><select id="expr-mouth">${mouthOptions}</select></div><div class="row"><label>Sobrancelha</label><select id="expr-brow">${browOptions}</select></div></div>`;
  html += `<div class="section"><div class="section-title">Visibilidade nesta expressão</div><div class="item-list">`;
  for (const part of project.parts) {
    const explicit = Object.hasOwn(expression.visibility || {}, part.id); const visible = explicit ? expression.visibility[part.id] : part.visible;
    html += `<div class="item"><div class="item-main"><strong>${esc(part.name)}</strong><small>${explicit?'override':'herda peça'}</small></div><button data-expr-vis="${esc(part.id)}">${visible?'👁':'—'}</button></div>`;
  }
  return html + `</div></div>`;
}

function animatePanel() {
  const clip = activeClip() || project.animations[0]; if (clip && !renderer.previewClipId) renderer.previewClipId = clip.id;
  const options = project.animations.map(item=>`<option value="${esc(item.id)}" ${item.id===renderer.previewClipId?'selected':''}>${esc(item.name)}</option>`).join(''); const max = clip?.duration || 1;
  const selection = selectedIK() ? `IK: ${selectedIK().name}` : selectedBone() ? `Bone: ${selectedBone().name}` : selectedPart() ? `Peça: ${selectedPart().name}` : 'Nada selecionado';
  return `<div class="section timeline"><div class="timeline-head"><select id="clip-select">${options}</select><button data-act="add-clip">＋</button><button data-act="play">${playing?'⏸':'▶'}</button></div><input id="timeline" class="timeline-scrub" type="range" min="0" max="${max}" step="0.01" value="${clamp(renderer.previewTime,0,max)}"><div class="row"><label>Tempo</label><span>${renderer.previewTime.toFixed(2)} / ${max.toFixed(2)}s</span></div><div class="row"><label>Alvo</label><span>${esc(selection)}</span></div>${clip?`${textRow('Nome','clip-name',clip.name)}${numberRow('Duração','clip-duration',clip.duration,.1)}<div class="row"><button data-act="key-pose" class="primary">◆ Key pose</button><button data-act="delete-clip">Excluir clip</button></div>`:''}<small>Com a timeline aberta, mover peça, bone ou IK já atualiza o keyframe no tempo atual. “Key pose” grava todas as propriedades do alvo selecionado.</small></div>`;
}

function projectPanel() {
  return `<div class="section"><div class="section-title">Canvas</div>${numberRow('Largura','canvas-width',project.canvas.width,1)}${numberRow('Altura','canvas-height',project.canvas.height,1)}<div class="row"><label>Fundo</label><span class="color-chip"><input id="canvas-bg" type="color" value="${esc(project.canvas.background)}"></span></div></div><div class="section"><div class="row"><button data-act="save" class="primary">Salvar agora</button><button data-act="export">Exportar JSON</button></div><div class="row"><button data-act="runtime">Abrir Runtime</button><button data-act="reset-view">Enquadrar</button></div><small>JSON é só a serialização automática do Studio; você não precisa editar na mão.</small></div>`;
}

function bindPanel(root) {
  if (!root) return;
  root.querySelector('[data-act="import-assets"]')?.addEventListener('click', () => assetInput.click());
  root.querySelectorAll('[data-select-part]').forEach(el => el.addEventListener('click', event => { if (event.target.closest('button')) return; selectPart(el.dataset.selectPart); }));
  root.querySelectorAll('[data-layer-up]').forEach(el => el.addEventListener('click', () => mutate(()=>{ const p=project.parts.find(x=>x.id===el.dataset.layerUp); if(p)p.layer+=1; })));
  root.querySelectorAll('[data-layer-down]').forEach(el => el.addEventListener('click', () => mutate(()=>{ const p=project.parts.find(x=>x.id===el.dataset.layerDown); if(p)p.layer-=1; })));
  root.querySelectorAll('[data-select-bone]').forEach(el => el.addEventListener('click', () => selectBone(el.dataset.selectBone)));
  root.querySelectorAll('[data-select-ik]').forEach(el => el.addEventListener('click', () => { selectedIKId=el.dataset.selectIk; renderer.showIK=true; renderer.showRig=true; renderPanel(); }));
  root.querySelectorAll('[data-color]').forEach(input => {
    input.addEventListener('input', () => mutate(()=>{ const slot=project.color_slots.find(x=>x.id===input.dataset.color);if(slot)slot.value=input.value; }, {history:false,panel:false}));
    input.addEventListener('change', recordHistory);
  });
  root.querySelectorAll('[data-expr-vis]').forEach(button => button.addEventListener('click', () => mutate(()=>{ const e=currentExpression();const part=project.parts.find(x=>x.id===button.dataset.exprVis);if(!e||!part)return;const current=Object.hasOwn(e.visibility,part.id)?e.visibility[part.id]:part.visible;e.visibility[part.id]=!current; })));
  root.querySelectorAll('[data-act]').forEach(button => button.addEventListener('click', () => handleAction(button.dataset.act)));

  bindValue(root,'part-name',v=>{const p=selectedPart();if(p)p.name=v;}); bindNumber(root,'part-layer',v=>{const p=selectedPart();if(p)p.layer=v;});
  const partBone = root.querySelector('#part-bone'); if(partBone) partBone.addEventListener('change',()=>mutate(()=>renderer.reparentPartPreserveWorld(selectedPartId,partBone.value)));
  bindValue(root,'part-tint',v=>{const p=selectedPart();if(p)p.tint_slot=v||null;});
  bindNumber(root,'part-x',v=>{const p=selectedPart();if(p)p.transform.x=v;}); bindNumber(root,'part-y',v=>{const p=selectedPart();if(p)p.transform.y=v;}); bindNumber(root,'part-rot',v=>{const p=selectedPart();if(p)p.transform.rotation=v*Math.PI/180;});
  bindNumber(root,'part-sx',v=>{const p=selectedPart();if(p)p.transform.scale_x=v;}); bindNumber(root,'part-sy',v=>{const p=selectedPart();if(p)p.transform.scale_y=v;}); bindNumber(root,'part-px',v=>{const p=selectedPart();if(p)p.transform.pivot_x=clamp(v,0,1);}); bindNumber(root,'part-py',v=>{const p=selectedPart();if(p)p.transform.pivot_y=clamp(v,0,1);});
  bindValue(root,'part-tags',v=>{const p=selectedPart();if(p)p.tags=v.split(',').map(x=>x.trim()).filter(Boolean);});
  bindNumber(root,'physics-stiffness',v=>{const p=selectedPhysics();if(p)p.stiffness=clamp(v,0,1);}); bindNumber(root,'physics-damping',v=>{const p=selectedPhysics();if(p)p.damping=clamp(v,0,1);}); bindNumber(root,'physics-gravity',v=>{const p=selectedPhysics();if(p)p.gravity=clamp(v,-4,4);}); bindNumber(root,'physics-angle',v=>{const p=selectedPhysics();if(p)p.max_angle=clamp(v*Math.PI/180,0,Math.PI);});

  bindValue(root,'bone-name',v=>{const b=selectedBone();if(b)b.name=v;}); bindValue(root,'bone-parent',v=>{const b=selectedBone();if(b)b.parent_id=v||null;}); bindNumber(root,'bone-x',v=>{const b=selectedBone();if(b)b.x=v;}); bindNumber(root,'bone-y',v=>{const b=selectedBone();if(b)b.y=v;}); bindNumber(root,'bone-rot',v=>{const b=selectedBone();if(b)b.rotation=v*Math.PI/180;}); bindNumber(root,'bone-length',v=>{const b=selectedBone();if(b)b.length=Math.max(0,v);});
  bindNumber(root,'ik-x',v=>{const c=selectedIK();if(c){c.target.x=v;renderer.solveIK(c.id,c.target);keyIKIfEditing(c);}}); bindNumber(root,'ik-y',v=>{const c=selectedIK();if(c){c.target.y=v;renderer.solveIK(c.id,c.target);keyIKIfEditing(c);}}); bindValue(root,'ik-bend',v=>{const c=selectedIK();if(c){c.bend=v;renderer.solveIK(c.id,c.target);keyIKIfEditing(c);}});

  const exprSelect = root.querySelector('#expr-select'); if(exprSelect) exprSelect.addEventListener('change',()=>mutate(()=>{project.metadata.editor_expression=exprSelect.value;renderer.expressionOverrideId=exprSelect.value;},{history:false,panel:true}));
  bindValue(root,'expr-name',v=>{const e=currentExpression();if(e)e.name=v;}); bindNumber(root,'expr-blush',v=>{const e=currentExpression();if(e)e.blush_opacity=clamp(v,0,1);}); bindValue(root,'expr-mouth',v=>{const e=currentExpression();if(e)e.mouth_part_id=v||null;}); bindValue(root,'expr-brow',v=>{const e=currentExpression();if(e)e.brow_part_id=v||null;});

  const clipSelect = root.querySelector('#clip-select'); if(clipSelect) clipSelect.addEventListener('change',()=>{renderer.previewClipId=clipSelect.value;renderer.previewTime=0;playing=false;renderPanel();});
  const timeline = root.querySelector('#timeline'); if(timeline) timeline.addEventListener('input',()=>{playing=false;renderer.previewTime=Number(timeline.value);renderPanel();});
  bindValue(root,'clip-name',v=>{const c=activeClip();if(c)c.name=v;}); bindNumber(root,'clip-duration',v=>{const c=activeClip();if(c)c.duration=clamp(v,.1,120);});
  bindNumber(root,'canvas-width',v=>{project.canvas.width=clamp(Math.round(v),64,4096);}); bindNumber(root,'canvas-height',v=>{project.canvas.height=clamp(Math.round(v),64,4096);}); bindValue(root,'canvas-bg',v=>{project.canvas.background=v;},true,false);
}

function bindValue(root,id,apply,capture=true,panel=false) {
  const el=root.querySelector(`#${id}`);if(!el)return;el.addEventListener('change',()=>mutate(()=>apply(el.value),{history:capture,panel}));
}
function bindNumber(root,id,apply){bindValue(root,id,value=>apply(Number(value)));}
function currentExpression(){const id=project.metadata.editor_expression||'neutral';return project.expressions.find(x=>x.id===id)||project.expressions[0]||null;}
function selectPart(id){selectedPartId=id;renderer.selectedPartId=id;renderPanel();}
function selectBone(id){selectedBoneId=id;renderer.selectedBoneId=id;renderer.showRig=true;renderPanel();}

function setTrackKey(clip,targetId,property,value,time=renderer.previewTime){
  let track=clip.tracks.find(t=>t.target_id===targetId&&t.property===property);
  if(!track){track={target_id:targetId,property,keyframes:[]};clip.tracks.push(track);}
  let key=track.keyframes.find(k=>Math.abs(k.time-time)<.005);
  if(key)key.value=value;else track.keyframes.push({time,value,easing:'ease_in_out'});
  track.keyframes.sort((a,b)=>a.time-b.time);
}
function keyPartIfEditing(part){const clip=activePanel==='animate'&&activeClip();if(!clip||!part)return;setTrackKey(clip,part.id,'transform.x',part.transform.x);setTrackKey(clip,part.id,'transform.y',part.transform.y);}
function keyBoneIfEditing(bone){const clip=activePanel==='animate'&&activeClip();if(!clip||!bone)return;setTrackKey(clip,bone.id,'x',bone.x);setTrackKey(clip,bone.id,'y',bone.y);}
function keyIKIfEditing(chain){const clip=activePanel==='animate'&&activeClip();if(!clip||!chain)return;const root=project.bones.find(b=>b.id===chain.root_bone_id);const joint=project.bones.find(b=>b.id===chain.joint_bone_id);if(root)setTrackKey(clip,root.id,'rotation',root.rotation);if(joint)setTrackKey(clip,joint.id,'rotation',joint.rotation);}

async function handleAction(action) {
  if(action==='save')return saveProject(true); if(action==='runtime')return window.open('/avatar/?mode=runtime','_blank'); if(action==='reset-view')return renderer.fit();
  if(action==='toggle-part')return mutate(()=>{const p=selectedPart();if(p)p.visible=!p.visible;});
  if(action==='delete-part')return mutate(()=>{project.physics=project.physics.filter(x=>x.target_part_id!==selectedPartId);project.parts=project.parts.filter(x=>x.id!==selectedPartId);selectedPartId=null;renderer.selectedPartId=null;});
  if(action==='humanoid')return mutate(()=>{project.bones=humanoidTemplate(project.canvas.width,project.canvas.height);project.ik_chains=[];selectedBoneId='root';renderer.selectedBoneId='root';renderer.showRig=true;});
  if(action==='standard-ik')return mutate(()=>{project.ik_chains=standardIK(project);selectedIKId=project.ik_chains[0]?.id||null;renderer.showIK=true;renderer.showRig=true;});
  if(action==='enable-physics')return mutate(()=>{const part=selectedPart();if(!part)return;project.physics.push({id:uniqueId(`physics_${part.id}`,project.physics),target_part_id:part.id,enabled:true,stiffness:.18,damping:.78,gravity:.3,max_angle:.22});});
  if(action==='disable-physics')return mutate(()=>{project.physics=project.physics.filter(x=>x.target_part_id!==selectedPartId);});
  if(action==='add-color')return mutate(()=>{const id=uniqueId('custom',project.color_slots);project.color_slots.push({id,name:'Nova cor',value:'#ffffff'});});
  if(action==='add-expression')return mutate(()=>{const id=uniqueId('expression',project.expressions);project.expressions.push({id,name:'Nova expressão',visibility:{},part_opacity:{},mouth_part_id:null,brow_part_id:null,blush_opacity:0});project.metadata.editor_expression=id;renderer.expressionOverrideId=id;});
  if(action==='add-clip')return mutate(()=>{const id=uniqueId('animation',project.animations);project.animations.push({id,name:'Nova animação',duration:1,loop:true,tracks:[]});renderer.previewClipId=id;renderer.previewTime=0;});
  if(action==='delete-clip')return mutate(()=>{const id=renderer.previewClipId;project.animations=project.animations.filter(x=>x.id!==id);renderer.previewClipId=project.animations[0]?.id||null;renderer.previewTime=0;});
  if(action==='play'){playing=!playing;lastFrame=performance.now();return renderPanel();}
  if(action==='key-pose')return keyCurrentPose();
  if(action==='export'){const blob=new Blob([JSON.stringify(project,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='amora_avatar.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
}

function keyCurrentPose(){
  const clip=activeClip();if(!clip)return toast('Escolha um clip');
  const chain=selectedIK();const bone=selectedBone();const part=selectedPart();
  mutate(()=>{
    if(chain){const root=project.bones.find(b=>b.id===chain.root_bone_id);const joint=project.bones.find(b=>b.id===chain.joint_bone_id);if(root){setTrackKey(clip,root.id,'rotation',root.rotation);}if(joint){setTrackKey(clip,joint.id,'rotation',joint.rotation);}}
    else if(bone){for(const [prop,value] of Object.entries({x:bone.x,y:bone.y,rotation:bone.rotation,length:bone.length}))setTrackKey(clip,bone.id,prop,value);}
    else if(part){for(const [prop,value] of Object.entries({'transform.x':part.transform.x,'transform.y':part.transform.y,'transform.rotation':part.transform.rotation,'transform.scale_x':part.transform.scale_x,'transform.scale_y':part.transform.scale_y}))setTrackKey(clip,part.id,prop,value);}
    else return;
  });
  toast(`Key pose @ ${renderer.previewTime.toFixed(2)}s`);
}

function suggestPartConfig(fileName) {
  const name=normalizedName(fileName);const tags=[];let tint=null;let bone=null;
  const has=(...words)=>words.some(word=>name.includes(word));
  if(has('cabelo','hair'))tint='hair_primary';
  if(has('vestido','dress'))tint=has('alca','strap')?'dress_straps':'dress';
  if(has('sapato','shoe','boot','bota'))tint='shoes';
  if(has('pulseira','bracelet','accessory','acessorio'))tint='accessory';
  if(has('blush','corado','bochecha')){tint='blush';tags.push('blush');}
  if(has('pupila','pupil','iris')){tint='eyes';tags.push('eye:pupil');}
  if(has('lash','cilio','eyelash'))tags.push(has('down','bottom','baixo','inferior')?'eyelash:bottom':'eyelash:top');
  if(has('boca','mouth')){
    if(has('fala','talk','speaking','falando'))tags.push('mouth:talk');else if(has('raiva','angry'))tags.push('mouth:angry');else if(has('triste','sad'))tags.push('mouth:sad');else if(has('lingua','tongue'))tags.push('mouth:tongue');else tags.push('mouth:neutral');
  }
  if(has('sobrancelha','brow'))tags.push(has('raiva','angry')?'brow:angry':'brow:neutral');
  if(has('cabeca','head','rosto','face')){tint=tint||'skin';tags.push('head');bone='head';}
  if(has('torso','body','corpo','peito','chest')){tint=tint||'skin';tags.push('body');bone='torso';}
  if(has('mao_esq','hand_l','left_hand')){tint=tint||'skin';bone='hand_l';} if(has('mao_dir','hand_r','right_hand')){tint=tint||'skin';bone='hand_r';}
  if(has('antebraco_esq','forearm_l')){tint=tint||'skin';bone='forearm_l';} if(has('antebraco_dir','forearm_r')){tint=tint||'skin';bone='forearm_r';}
  if(has('braco_esq','upper_arm_l')){tint=tint||'skin';bone='upper_arm_l';} if(has('braco_dir','upper_arm_r')){tint=tint||'skin';bone='upper_arm_r';}
  if(has('coxa_esq','thigh_l')){tint=tint||'skin';bone='thigh_l';} if(has('coxa_dir','thigh_r')){tint=tint||'skin';bone='thigh_r';}
  if(has('canela_esq','shin_l','perna_esq')){tint=tint||'skin';bone='shin_l';} if(has('canela_dir','shin_r','perna_dir')){tint=tint||'skin';bone='shin_r';}
  if(has('pele','skin'))tint='skin';
  return {tags:[...new Set(tags)],tint_slot:tint,bone_id:bone};
}

assetInput.addEventListener('change', async () => {
  const files=[...(assetInput.files||[])];assetInput.value='';if(!files.length)return;
  for(const file of files){const base=idSafe(file.name.replace(/\.[^.]+$/,''));const id=uniqueId(base,project.assets);try{
    await api(`/v1/avatar/assets/${encodeURIComponent(id)}`,{method:'PUT',headers:{'Content-Type':file.type||'image/png'},body:await file.arrayBuffer()});
    const image=await loadImageAsset(id);assets.set(id,image);renderer.setAssets(assets);const suggestion=suggestPartConfig(file.name);
    mutate(()=>{project.assets.push({id,name:file.name,content_type:file.type||'image/png'});const partId=uniqueId(id,project.parts);project.parts.push({id:partId,name:file.name.replace(/\.[^.]+$/,''),asset_id:id,layer:project.parts.length,bone_id:project.bones.some(b=>b.id===suggestion.bone_id)?suggestion.bone_id:null,tint_slot:suggestion.tint_slot,visible:true,transform:{x:project.canvas.width/2,y:project.canvas.height/2,rotation:0,scale_x:1,scale_y:1,pivot_x:.5,pivot_y:.5,opacity:1},tags:suggestion.tags});selectedPartId=partId;renderer.selectedPartId=partId;});
  }catch(error){toast(`Asset ${file.name}: ${error.message}`);}}
  toast(`${files.length} asset(s) importado(s)`);
});

function nearestBone(point,radius=35/renderer.camera.scale){if(!project)return null;renderer._boneCache.clear();let best=null,dist=Infinity;for(const bone of project.bones){const world=renderer._boneWorld(bone.id,new Map());if(!world)continue;const d=Math.hypot(point.x-world.x,point.y-world.y);if(d<dist&&d<=radius){best=bone;dist=d;}}return best;}
function nearestIK(point,radius=42/renderer.camera.scale){let best=null,dist=Infinity;for(const chain of project.ik_chains||[]){const d=Math.hypot(point.x-chain.target.x,point.y-chain.target.y);if(d<dist&&d<=radius){best=chain;dist=d;}}return best;}

canvas.addEventListener('pointerdown',event=>{
  canvas.setPointerCapture(event.pointerId);pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,px:event.clientX,py:event.clientY});
  if(pointers.size===2){const pts=[...pointers.values()];gesture={type:'pinch',distance:Math.hypot(pts[0].x-pts[1].x,pts[0].y-pts[1].y),cx:(pts[0].x+pts[1].x)/2,cy:(pts[0].y+pts[1].y)/2};return;}
  const point=renderer.clientToProject(event.clientX,event.clientY);
  if(activeTool==='ik'){const chain=nearestIK(point);if(chain){selectedIKId=chain.id;gesture={type:'ik',id:chain.id,last:point,changed:false};renderPanel();return;}}
  if(activeTool==='bone'){const bone=nearestBone(point);if(bone){selectBone(bone.id);gesture={type:'bone',id:bone.id,last:point,changed:false};return;}}
  const hit=renderer.pickPart(point);if(hit){selectPart(hit.id);gesture={type:activeTool==='pivot'?'pivot':activeTool==='move'?'move':'select',id:hit.id,last:point,changed:false};}else gesture={type:'pan',lastClient:{x:event.clientX,y:event.clientY}};
});

canvas.addEventListener('pointermove',event=>{
  const ptr=pointers.get(event.pointerId);if(!ptr)return;ptr.px=ptr.x;ptr.py=ptr.y;ptr.x=event.clientX;ptr.y=event.clientY;
  if(pointers.size>=2){const pts=[...pointers.values()].slice(0,2);const distance=Math.max(1,Math.hypot(pts[0].x-pts[1].x,pts[0].y-pts[1].y));const cx=(pts[0].x+pts[1].x)/2,cy=(pts[0].y+pts[1].y)/2;if(gesture?.type==='pinch'){renderer.zoomAt(distance/gesture.distance,cx,cy);renderer.pan(cx-gesture.cx,cy-gesture.cy);}gesture={type:'pinch',distance,cx,cy};return;}
  if(!gesture)return;const point=renderer.clientToProject(event.clientX,event.clientY);
  if(gesture.type==='pan'){renderer.pan(event.clientX-gesture.lastClient.x,event.clientY-gesture.lastClient.y);gesture.lastClient={x:event.clientX,y:event.clientY};return;}
  if(gesture.type==='move'){const p=project.parts.find(x=>x.id===gesture.id);if(p){renderer.movePartWorldDelta(p.id,point.x-gesture.last.x,point.y-gesture.last.y);keyPartIfEditing(p);gesture.last=point;gesture.changed=true;dirty=true;scheduleSave();}return;}
  if(gesture.type==='pivot'){const p=project.parts.find(x=>x.id===gesture.id);const image=p&&assets.get(p.asset_id);if(p&&image){renderer.setPartPivotAtWorld(p.id,point,image);gesture.last=point;gesture.changed=true;dirty=true;scheduleSave();}return;}
  if(gesture.type==='ik'){const chain=project.ik_chains.find(x=>x.id===gesture.id);if(chain){renderer.solveIK(chain.id,point);keyIKIfEditing(chain);gesture.last=point;gesture.changed=true;dirty=true;scheduleSave();}return;}
  if(gesture.type==='bone'){const bone=project.bones.find(x=>x.id===gesture.id);if(bone){renderer.moveBoneWorldDelta(bone.id,point.x-gesture.last.x,point.y-gesture.last.y);keyBoneIfEditing(bone);gesture.last=point;gesture.changed=true;dirty=true;scheduleSave();}}
});

function pointerEnd(event){const changed=Boolean(gesture?.changed);pointers.delete(event.pointerId);if(pointers.size<2&&gesture?.type==='pinch')gesture=null;if(pointers.size===0){if(changed)recordHistory();if(dirty)renderPanel();gesture=null;}}
canvas.addEventListener('pointerup',pointerEnd);canvas.addEventListener('pointercancel',pointerEnd);
canvas.addEventListener('wheel',event=>{event.preventDefault();renderer.zoomAt(Math.exp(-event.deltaY*.001),event.clientX,event.clientY);},{passive:false});

async function pollRuntimeState(){if(!runtimeMode||!token)return;try{const state=await api('/v1/avatar/state');renderer.setSemanticState(state);renderer.expressionOverrideId=null;renderer.previewClipId=state.speaking&&project.animations.some(x=>x.id==='talk')?'talk':project.animations.some(x=>x.id==='idle')?'idle':null;connectionPill.textContent='live';connectionPill.classList.add('ok');}catch(_) {}}

async function bootstrap(){if(!token){showPair();return;}try{project=await api('/v1/avatar/project');renderer.setProject(project);await loadAssets();renderer.fit();connectionPill.textContent='Core ok';connectionPill.classList.add('ok');hidePair();if(!runtimeMode){history=[JSON.stringify(project)];historyIndex=0;renderer.previewClipId=project.animations[0]?.id||null;renderer.expressionOverrideId=project.metadata.editor_expression||'neutral';renderPanel();}else{clearInterval(runtimePoll);await pollRuntimeState();runtimePoll=setInterval(pollRuntimeState,250);}}catch(error){if(String(error.message)!=='pairing required')toast(`Core: ${error.message}`);}}

function frame(now){if(playing){const clip=activeClip();if(clip){renderer.previewTime+=(now-lastFrame)/1000;if(renderer.previewTime>clip.duration)renderer.previewTime=clip.loop?renderer.previewTime%clip.duration:clip.duration;}}lastFrame=now;renderer.render(now);zoomLabel.textContent=`${Math.round(renderer.camera.scale*100)}%`;requestAnimationFrame(frame);}
window.addEventListener('resize',()=>renderer.resize());
document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty)saveProject(false);});
if('serviceWorker' in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});
requestAnimationFrame(frame);bootstrap();
