// Amora Avatar Studio / Runtime V0.9
// @ts-check
import { AvatarRenderer, clamp, humanoidTemplate, standardIK } from './engine.js';

const qs = new URLSearchParams(location.search);
const runtimeMode = qs.get('mode') === 'runtime';
document.body.classList.toggle('runtime', runtimeMode);

const canvas = /** @type {HTMLCanvasElement} */ (document.querySelector('#stage'));
const renderer = new AvatarRenderer(canvas);
const viewport = document.querySelector('#viewport');
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

let token = localStorage.getItem('amora.avatar.token') || '';
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

function clone(value) { return structuredClone(value); }
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function idSafe(value) {
  const clean = String(value).normalize('NFKD').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 72);
  return clean || `asset_${Date.now()}`;
}
function uniqueId(base, list) {
  const used = new Set(list.map(item => item.id));
  if (!used.has(base)) return base;
  let i = 2;
  while (used.has(`${base}_${i}`)) i += 1;
  return `${base}_${i}`;
}
function selectedPart() { return project?.parts.find(item => item.id === selectedPartId) || null; }
function selectedBone() { return project?.bones.find(item => item.id === selectedBoneId) || null; }
function selectedIK() { return project?.ik_chains.find(item => item.id === selectedIKId) || null; }
function activeClip() { return project?.animations.find(item => item.id === renderer.previewClipId) || null; }

function toast(text) {
  toastEl.textContent = text;
  toastEl.classList.add('show');
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.remove('show'), 1800);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    token = '';
    localStorage.removeItem('amora.avatar.token');
    showPair();
    throw new Error('pairing required');
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
    const response = await fetch('/v1/pairing/redeem', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({code}),
    });
    if (!response.ok) throw new Error('Código inválido ou expirado');
    const data = await response.json();
    token = data.token;
    localStorage.setItem('amora.avatar.token', token);
    hidePair();
    pairCode.value = '';
    await bootstrap();
  } catch (error) { toast(error.message || 'Falha ao parear'); }
});

async function loadImageAsset(assetId) {
  const response = await api(`/v1/avatar/assets/${encodeURIComponent(assetId)}`);
  const blob = await response.blob();
  try { return await createImageBitmap(blob); }
  catch (_) {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.src = url;
    await image.decode();
    URL.revokeObjectURL(url);
    return image;
  }
}

async function loadAssets() {
  const next = new Map();
  await Promise.all((project.assets || []).map(async ref => {
    try { next.set(ref.id, await loadImageAsset(ref.id)); }
    catch (error) { console.warn('Asset não carregou', ref.id, error); }
  }));
  assets = next;
  renderer.setAssets(assets);
}

function pushHistory() {
  if (!project || runtimeMode) return;
  const snapshot = JSON.stringify(project);
  if (history[historyIndex] === snapshot) return;
  history = history.slice(0, historyIndex + 1);
  history.push(snapshot);
  if (history.length > 40) history.shift();
  historyIndex = history.length - 1;
}

function mutate(fn, {history: capture = true, panel = true} = {}) {
  if (!project) return;
  if (capture) pushHistory();
  fn();
  renderer.setProject(project);
  dirty = true;
  scheduleSave();
  if (panel) renderPanel();
}

function scheduleSave() {
  if (runtimeMode) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveProject(false), 850);
}

async function saveProject(notify = true) {
  if (!project || runtimeMode) return;
  clearTimeout(saveTimer);
  try {
    project = await api('/v1/avatar/project', {
      method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(project),
    });
    renderer.setProject(project);
    dirty = false;
    connectionPill.textContent = 'salvo';
    connectionPill.classList.add('ok');
    if (notify) toast('Projeto salvo no Core');
  } catch (error) {
    connectionPill.textContent = 'erro ao salvar';
    connectionPill.classList.remove('ok');
    if (notify) toast(`Salvar: ${error.message}`);
  }
}

function restoreHistory(delta) {
  const next = historyIndex + delta;
  if (next < 0 || next >= history.length) return;
  historyIndex = next;
  project = JSON.parse(history[historyIndex]);
  renderer.setProject(project);
  dirty = true;
  scheduleSave();
  renderPanel();
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
    renderer.showRig = ['bone','ik'].includes(activeTool);
    renderer.showIK = activeTool === 'ik';
    if (activeTool === 'bone') openPanel('rig');
    if (activeTool === 'ik') openPanel('rig');
  });
}
for (const button of document.querySelectorAll('[data-panel]')) button.addEventListener('click', () => openPanel(button.dataset.panel));

function openPanel(name) {
  activePanel = name || 'parts';
  document.querySelectorAll('.bottom-nav [data-panel]').forEach(item => item.classList.toggle('active', item.dataset.panel === activePanel));
  sheet.classList.add('open');
  renderPanel();
}

function panelName(name) {
  return ({parts:'Peças',layers:'Layers',rig:'Rig + IK',colors:'Customização',expressions:'Expressões',animate:'Timeline',project:'Projeto'})[name] || 'Studio';
}

function renderPanel() {
  if (!project || runtimeMode) return;
  const html = panelHtml(activePanel);
  sheetTitle.textContent = panelName(activePanel);
  sheetBody.innerHTML = html;
  desktopInspector.innerHTML = `<div style="padding:12px"><h3>${esc(panelName(activePanel))}</h3>${html}</div>`;
  bindPanel(sheetBody);
  bindPanel(desktopInspector);
}

function panelHtml(name) {
  if (name === 'parts' || name === 'layers') return partsPanel();
  if (name === 'rig') return rigPanel();
  if (name === 'colors') return colorsPanel();
  if (name === 'expressions') return expressionsPanel();
  if (name === 'animate') return animatePanel();
  return projectPanel();
}

function partsPanel() {
  const part = selectedPart();
  const ordered = [...project.parts].sort((a,b) => b.layer - a.layer);
  let html = `<div class="section"><div class="row"><button data-act="import-assets" class="primary">＋ Importar PNGs</button><span class="badge">${project.parts.length} peças</span></div><div class="item-list">`;
  for (const item of ordered) {
    html += `<div class="item ${item.id===selectedPartId?'selected':''}" data-select-part="${esc(item.id)}"><div class="item-main"><strong>${esc(item.name)}</strong><small>layer ${item.layer} · ${esc(item.bone_id || 'sem bone')} · ${esc(item.tint_slot || 'cor original')}</small></div><div class="item-actions"><button data-layer-up="${esc(item.id)}">↑</button><button data-layer-down="${esc(item.id)}">↓</button></div></div>`;
  }
  html += `</div></div>`;
  if (!part) return html + `<div class="section"><small>Toque numa peça no canvas ou na lista para editar.</small></div>`;
  const boneOptions = [`<option value="">sem bone</option>`, ...project.bones.map(b => `<option value="${esc(b.id)}" ${b.id===part.bone_id?'selected':''}>${esc(b.name)}</option>`)].join('');
  const tintOptions = [`<option value="">cor original</option>`, ...project.color_slots.map(s => `<option value="${esc(s.id)}" ${s.id===part.tint_slot?'selected':''}>${esc(s.name)}</option>`)].join('');
  html += `<div class="section"><div class="section-title">${esc(part.name)}</div>
    ${textRow('Nome','part-name',part.name)}
    ${numberRow('Layer','part-layer',part.layer,1)}
    <div class="row"><label>Bone</label><select id="part-bone">${boneOptions}</select></div>
    <div class="row"><label>Cor</label><select id="part-tint">${tintOptions}</select></div>
    ${numberRow('X','part-x',part.transform.x,.5)}${numberRow('Y','part-y',part.transform.y,.5)}
    ${numberRow('Rot °','part-rot',(part.transform.rotation*180/Math.PI).toFixed(1),1)}
    ${numberRow('Escala X','part-sx',part.transform.scale_x,.01)}${numberRow('Escala Y','part-sy',part.transform.scale_y,.01)}
    ${numberRow('Pivô X','part-px',part.transform.pivot_x,.01)}${numberRow('Pivô Y','part-py',part.transform.pivot_y,.01)}
    ${textRow('Tags','part-tags',(part.tags||[]).join(', '))}
    <div class="row"><button data-act="toggle-part">${part.visible?'Ocultar':'Mostrar'}</button><button data-act="delete-part">Excluir peça</button></div>
  </div>`;
  return html;
}

function rigPanel() {
  const bone = selectedBone();
  const chain = selectedIK();
  let html = `<div class="section"><div class="row"><button data-act="humanoid" class="primary">Gerar rig humanoide</button><button data-act="standard-ik">Criar IK padrão</button></div><small>O template cria hierarchy; ajuste articulações visualmente depois.</small></div>`;
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
  html += `</div>`;
  return html;
}

function colorsPanel() {
  return `<div class="section"><div class="section-title">Slots de cor</div>${project.color_slots.map(slot => `<div class="row"><label>${esc(slot.name)}</label><span class="color-chip"><input type="color" value="${esc(slot.value)}" data-color="${esc(slot.id)}"></span><code>${esc(slot.value)}</code></div>`).join('')}<div class="row"><button data-act="add-color">＋ slot</button></div></div>`;
}

function expressionsPanel() {
  const selected = project.metadata.editor_expression || 'neutral';
  const expression = project.expressions.find(x=>x.id===selected) || project.expressions[0];
  if (!expression) return `<button data-act="add-expression">Criar expressão</button>`;
  const expressionOptions = project.expressions.map(item=>`<option value="${esc(item.id)}" ${item.id===expression.id?'selected':''}>${esc(item.name)}</option>`).join('');
  const partOptions = [`<option value="">nenhum</option>`, ...project.parts.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`)].join('');
  let html = `<div class="section"><div class="row"><label>Expressão</label><select id="expr-select">${expressionOptions}</select><button data-act="add-expression">＋</button></div>${textRow('Nome','expr-name',expression.name)}${numberRow('Blush','expr-blush',expression.blush_opacity ?? 0,.05)}</div>`;
  html += `<div class="section"><div class="section-title">Visibilidade nesta expressão</div><div class="item-list">`;
  for (const part of project.parts) {
    const explicit = Object.hasOwn(expression.visibility || {}, part.id);
    const visible = explicit ? expression.visibility[part.id] : part.visible;
    html += `<div class="item"><div class="item-main"><strong>${esc(part.name)}</strong><small>${explicit?'override':'herda peça'}</small></div><button data-expr-vis="${esc(part.id)}">${visible?'👁':'—'}</button></div>`;
  }
  html += `</div><div class="row"><label>Boca</label><select id="expr-mouth">${partOptions.replace(`value="${expression.mouth_part_id||''}"`, `value="${expression.mouth_part_id||''}" selected`)}</select></div></div>`;
  return html;
}

function animatePanel() {
  const clip = activeClip() || project.animations[0];
  if (clip && !renderer.previewClipId) renderer.previewClipId = clip.id;
  const options = project.animations.map(item=>`<option value="${esc(item.id)}" ${item.id===renderer.previewClipId?'selected':''}>${esc(item.name)}</option>`).join('');
  const max = clip?.duration || 1;
  return `<div class="section timeline"><div class="timeline-head"><select id="clip-select">${options}</select><button data-act="add-clip">＋</button><button data-act="play">${playing?'⏸':'▶'}</button></div><input id="timeline" class="timeline-scrub" type="range" min="0" max="${max}" step="0.01" value="${clamp(renderer.previewTime,0,max)}"><div class="row"><label>Tempo</label><span>${renderer.previewTime.toFixed(2)} / ${max.toFixed(2)}s</span></div>${clip?`${textRow('Nome','clip-name',clip.name)}${numberRow('Duração','clip-duration',clip.duration,.1)}<div class="row"><button data-act="key-pose" class="primary">◆ Key pose da peça</button><button data-act="delete-clip">Excluir clip</button></div>`:''}<small>Selecione uma peça, posicione visualmente, mova o scrubber e toque em “Key pose”. X/Y/rotação/escala são salvos automaticamente.</small></div>`;
}

function projectPanel() {
  return `<div class="section"><div class="section-title">Canvas</div>${numberRow('Largura','canvas-width',project.canvas.width,1)}${numberRow('Altura','canvas-height',project.canvas.height,1)}<div class="row"><label>Fundo</label><span class="color-chip"><input id="canvas-bg" type="color" value="${esc(project.canvas.background)}"></span></div></div><div class="section"><div class="row"><button data-act="save" class="primary">Salvar agora</button><button data-act="export">Exportar JSON</button></div><div class="row"><button data-act="runtime">Abrir Runtime</button><button data-act="reset-view">Enquadrar</button></div><small>JSON é serialização automática do Studio; você não precisa editar na mão.</small></div>`;
}

function textRow(label,id,value) { return `<div class="row"><label>${esc(label)}</label><input id="${id}" type="text" value="${esc(value)}"></div>`; }
function numberRow(label,id,value,step) { return `<div class="row"><label>${esc(label)}</label><input id="${id}" type="number" step="${step}" value="${esc(value)}"></div>`; }

function bindPanel(root) {
  if (!root) return;
  root.querySelector('[data-act="import-assets"]')?.addEventListener('click', () => assetInput.click());
  root.querySelectorAll('[data-select-part]').forEach(el => el.addEventListener('click', event => { if (event.target.closest('button')) return; selectPart(el.dataset.selectPart); }));
  root.querySelectorAll('[data-layer-up]').forEach(el => el.addEventListener('click', () => mutate(()=>{ const p=project.parts.find(x=>x.id===el.dataset.layerUp); if(p)p.layer+=1; }))); 
  root.querySelectorAll('[data-layer-down]').forEach(el => el.addEventListener('click', () => mutate(()=>{ const p=project.parts.find(x=>x.id===el.dataset.layerDown); if(p)p.layer-=1; }))); 
  root.querySelectorAll('[data-select-bone]').forEach(el => el.addEventListener('click', () => selectBone(el.dataset.selectBone)));
  root.querySelectorAll('[data-select-ik]').forEach(el => el.addEventListener('click', () => { selectedIKId=el.dataset.selectIk; renderer.showIK=true; renderPanel(); }));
  root.querySelectorAll('[data-color]').forEach(input => input.addEventListener('input', () => mutate(()=>{ const slot=project.color_slots.find(x=>x.id===input.dataset.color); if(slot)slot.value=input.value; }, {history:false,panel:false})));
  root.querySelectorAll('[data-expr-vis]').forEach(button => button.addEventListener('click', () => mutate(()=>{ const e=currentExpression(); const part=project.parts.find(x=>x.id===button.dataset.exprVis); if(!e||!part)return; const current=Object.hasOwn(e.visibility,part.id)?e.visibility[part.id]:part.visible; e.visibility[part.id]=!current; })));
  root.querySelectorAll('[data-act]').forEach(button => button.addEventListener('click', () => handleAction(button.dataset.act)));

  bindValue(root,'part-name',v=>{const p=selectedPart();if(p)p.name=v;});
  bindNumber(root,'part-layer',v=>{const p=selectedPart();if(p)p.layer=v;});
  bindValue(root,'part-bone',v=>{const p=selectedPart();if(p)p.bone_id=v||null;});
  bindValue(root,'part-tint',v=>{const p=selectedPart();if(p)p.tint_slot=v||null;});
  bindNumber(root,'part-x',v=>{const p=selectedPart();if(p)p.transform.x=v;}); bindNumber(root,'part-y',v=>{const p=selectedPart();if(p)p.transform.y=v;});
  bindNumber(root,'part-rot',v=>{const p=selectedPart();if(p)p.transform.rotation=v*Math.PI/180;});
  bindNumber(root,'part-sx',v=>{const p=selectedPart();if(p)p.transform.scale_x=v;}); bindNumber(root,'part-sy',v=>{const p=selectedPart();if(p)p.transform.scale_y=v;});
  bindNumber(root,'part-px',v=>{const p=selectedPart();if(p)p.transform.pivot_x=v;}); bindNumber(root,'part-py',v=>{const p=selectedPart();if(p)p.transform.pivot_y=v;});
  bindValue(root,'part-tags',v=>{const p=selectedPart();if(p)p.tags=v.split(',').map(x=>x.trim()).filter(Boolean);});

  bindValue(root,'bone-name',v=>{const b=selectedBone();if(b)b.name=v;}); bindValue(root,'bone-parent',v=>{const b=selectedBone();if(b)b.parent_id=v||null;});
  bindNumber(root,'bone-x',v=>{const b=selectedBone();if(b)b.x=v;}); bindNumber(root,'bone-y',v=>{const b=selectedBone();if(b)b.y=v;});
  bindNumber(root,'bone-rot',v=>{const b=selectedBone();if(b)b.rotation=v*Math.PI/180;}); bindNumber(root,'bone-length',v=>{const b=selectedBone();if(b)b.length=Math.max(0,v);});
  bindNumber(root,'ik-x',v=>{const c=selectedIK();if(c){c.target.x=v;renderer.solveIK(c.id,c.target);}}); bindNumber(root,'ik-y',v=>{const c=selectedIK();if(c){c.target.y=v;renderer.solveIK(c.id,c.target);}}); bindValue(root,'ik-bend',v=>{const c=selectedIK();if(c){c.bend=v;renderer.solveIK(c.id,c.target);}});

  bindValue(root,'expr-select',v=>mutate(()=>{project.metadata.editor_expression=v;},{history:false}));
  bindValue(root,'expr-name',v=>{const e=currentExpression();if(e)e.name=v;}); bindNumber(root,'expr-blush',v=>{const e=currentExpression();if(e)e.blush_opacity=clamp(v,0,1);});
  bindValue(root,'expr-mouth',v=>{const e=currentExpression();if(e)e.mouth_part_id=v||null;});

  bindValue(root,'clip-select',v=>{renderer.previewClipId=v;renderer.previewTime=0;renderPanel();},false);
  const timeline = root.querySelector('#timeline'); if(timeline) timeline.addEventListener('input',()=>{playing=false;renderer.previewTime=Number(timeline.value);renderPanel();});
  bindValue(root,'clip-name',v=>{const c=activeClip();if(c)c.name=v;}); bindNumber(root,'clip-duration',v=>{const c=activeClip();if(c)c.duration=clamp(v,.1,120);});
  bindNumber(root,'canvas-width',v=>{project.canvas.width=clamp(Math.round(v),64,4096);}); bindNumber(root,'canvas-height',v=>{project.canvas.height=clamp(Math.round(v),64,4096);});
  bindValue(root,'canvas-bg',v=>{project.canvas.background=v;},true,false);
}

function bindValue(root,id,apply,capture=true,panel=false) {
  const el = root.querySelector(`#${id}`); if(!el)return;
  el.addEventListener('change',()=>mutate(()=>apply(el.value),{history:capture,panel}));
}
function bindNumber(root,id,apply) { bindValue(root,id,value=>apply(Number(value))); }

function currentExpression() {
  const id = project.metadata.editor_expression || 'neutral';
  return project.expressions.find(x=>x.id===id) || project.expressions[0] || null;
}

function selectPart(id) {
  selectedPartId=id; renderer.selectedPartId=id; renderPanel();
}
function selectBone(id) {
  selectedBoneId=id; renderer.selectedBoneId=id; renderer.showRig=true; renderPanel();
}

async function handleAction(action) {
  if (action === 'save') return saveProject(true);
  if (action === 'runtime') return window.open('/avatar/?mode=runtime','_blank');
  if (action === 'reset-view') return renderer.fit();
  if (action === 'toggle-part') return mutate(()=>{const p=selectedPart();if(p)p.visible=!p.visible;});
  if (action === 'delete-part') return mutate(()=>{project.parts=project.parts.filter(x=>x.id!==selectedPartId);selectedPartId=null;renderer.selectedPartId=null;});
  if (action === 'humanoid') return mutate(()=>{project.bones=humanoidTemplate(project.canvas.width,project.canvas.height);project.ik_chains=[];selectedBoneId='root';renderer.selectedBoneId='root';renderer.showRig=true;});
  if (action === 'standard-ik') return mutate(()=>{project.ik_chains=standardIK(project);selectedIKId=project.ik_chains[0]?.id||null;renderer.showIK=true;});
  if (action === 'add-color') return mutate(()=>{const id=uniqueId('custom',project.color_slots);project.color_slots.push({id,name:'Nova cor',value:'#ffffff'});});
  if (action === 'add-expression') return mutate(()=>{const id=uniqueId('expression',project.expressions);project.expressions.push({id,name:'Nova expressão',visibility:{},part_opacity:{},mouth_part_id:null,brow_part_id:null,blush_opacity:0});project.metadata.editor_expression=id;});
  if (action === 'add-clip') return mutate(()=>{const id=uniqueId('animation',project.animations);project.animations.push({id,name:'Nova animação',duration:1,loop:true,tracks:[]});renderer.previewClipId=id;renderer.previewTime=0;});
  if (action === 'delete-clip') return mutate(()=>{const id=renderer.previewClipId;project.animations=project.animations.filter(x=>x.id!==id);renderer.previewClipId=project.animations[0]?.id||null;renderer.previewTime=0;});
  if (action === 'play') { playing=!playing; lastFrame=performance.now(); return renderPanel(); }
  if (action === 'key-pose') return keyCurrentPose();
  if (action === 'export') {
    const blob=new Blob([JSON.stringify(project,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='amora_avatar.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);return;
  }
}

function keyCurrentPose() {
  const part=selectedPart(); const clip=activeClip(); if(!part||!clip)return toast('Selecione uma peça e um clip');
  mutate(()=>{
    const values={
      'transform.x':part.transform.x,'transform.y':part.transform.y,'transform.rotation':part.transform.rotation,
      'transform.scale_x':part.transform.scale_x,'transform.scale_y':part.transform.scale_y,
    };
    for(const [property,value] of Object.entries(values)) {
      let track=clip.tracks.find(t=>t.target_id===part.id&&t.property===property);
      if(!track){track={target_id:part.id,property,keyframes:[]};clip.tracks.push(track);}
      const existing=track.keyframes.find(k=>Math.abs(k.time-renderer.previewTime)<.005);
      if(existing)existing.value=value;else track.keyframes.push({time:renderer.previewTime,value,easing:'ease_in_out'});
      track.keyframes.sort((a,b)=>a.time-b.time);
    }
  });
  toast(`Key pose @ ${renderer.previewTime.toFixed(2)}s`);
}

assetInput.addEventListener('change', async () => {
  const files=[...(assetInput.files||[])]; assetInput.value=''; if(!files.length)return;
  for(const file of files) {
    const base=idSafe(file.name.replace(/\.[^.]+$/,''));
    const id=uniqueId(base,project.assets);
    try {
      await api(`/v1/avatar/assets/${encodeURIComponent(id)}`,{method:'PUT',headers:{'Content-Type':file.type||'image/png'},body:await file.arrayBuffer()});
      const image=await loadImageAsset(id); assets.set(id,image); renderer.setAssets(assets);
      mutate(()=>{
        project.assets.push({id,name:file.name,content_type:file.type||'image/png'});
        project.parts.push({id:uniqueId(id,project.parts),name:file.name.replace(/\.[^.]+$/,''),asset_id:id,layer:project.parts.length,bone_id:null,tint_slot:null,visible:true,transform:{x:project.canvas.width/2,y:project.canvas.height/2,rotation:0,scale_x:1,scale_y:1,pivot_x:.5,pivot_y:.5,opacity:1},tags:[]});
        selectedPartId=project.parts.at(-1).id;renderer.selectedPartId=selectedPartId;
      });
    } catch(error) { toast(`Asset ${file.name}: ${error.message}`); }
  }
  toast(`${files.length} asset(s) importado(s)`);
});

function nearestBone(point, radius=35/renderer.camera.scale) {
  if(!project)return null; renderer._boneCache.clear(); let best=null; let dist=Infinity;
  for(const bone of project.bones){const world=renderer._boneWorld(bone.id,new Map());if(!world)continue;const d=Math.hypot(point.x-world.x,point.y-world.y);if(d<dist&&d<=radius){best=bone;dist=d;}}
  return best;
}
function nearestIK(point, radius=42/renderer.camera.scale) {
  let best=null,dist=Infinity;for(const chain of project.ik_chains||[]){const d=Math.hypot(point.x-chain.target.x,point.y-chain.target.y);if(d<dist&&d<=radius){best=chain;dist=d;}}return best;
}

canvas.addEventListener('pointerdown', event => {
  canvas.setPointerCapture(event.pointerId); pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,px:event.clientX,py:event.clientY});
  if(pointers.size===2){const pts=[...pointers.values()];gesture={type:'pinch',distance:Math.hypot(pts[0].x-pts[1].x,pts[0].y-pts[1].y),cx:(pts[0].x+pts[1].x)/2,cy:(pts[0].y+pts[1].y)/2};return;}
  const point=renderer.clientToProject(event.clientX,event.clientY);
  if(activeTool==='ik'){const chain=nearestIK(point);if(chain){selectedIKId=chain.id;gesture={type:'ik',id:chain.id,last:point};renderPanel();return;}}
  if(activeTool==='bone'){const bone=nearestBone(point);if(bone){selectBone(bone.id);gesture={type:'bone',id:bone.id,last:point};return;}}
  const hit=renderer.pickPart(point);
  if(hit){selectPart(hit.id);gesture={type:activeTool==='pivot'?'pivot':activeTool==='move'?'move':'select',id:hit.id,last:point};}
  else gesture={type:'pan',lastClient:{x:event.clientX,y:event.clientY}};
});

canvas.addEventListener('pointermove', event => {
  const ptr=pointers.get(event.pointerId);if(!ptr)return;ptr.px=ptr.x;ptr.py=ptr.y;ptr.x=event.clientX;ptr.y=event.clientY;
  if(pointers.size>=2){const pts=[...pointers.values()].slice(0,2);const distance=Math.max(1,Math.hypot(pts[0].x-pts[1].x,pts[0].y-pts[1].y));const cx=(pts[0].x+pts[1].x)/2,cy=(pts[0].y+pts[1].y)/2;if(gesture?.type==='pinch'){renderer.zoomAt(distance/gesture.distance,cx,cy);renderer.pan(cx-gesture.cx,cy-gesture.cy);}gesture={type:'pinch',distance,cx,cy};return;}
  if(!gesture)return;const point=renderer.clientToProject(event.clientX,event.clientY);
  if(gesture.type==='pan'){renderer.pan(event.clientX-gesture.lastClient.x,event.clientY-gesture.lastClient.y);gesture.lastClient={x:event.clientX,y:event.clientY};return;}
  if(gesture.type==='move'){const p=project.parts.find(x=>x.id===gesture.id);if(p){const dx=point.x-gesture.last.x,dy=point.y-gesture.last.y;p.transform.x+=dx;p.transform.y+=dy;gesture.last=point;dirty=true;scheduleSave();}return;}
  if(gesture.type==='pivot'){const p=project.parts.find(x=>x.id===gesture.id);const image=p&&assets.get(p.asset_id);if(p&&image){const dx=point.x-p.transform.x,dy=point.y-p.transform.y;p.transform.pivot_x=clamp(.5+dx/(image.width*Math.max(.01,p.transform.scale_x)),0,1);p.transform.pivot_y=clamp(.5+dy/(image.height*Math.max(.01,p.transform.scale_y)),0,1);gesture.last=point;dirty=true;scheduleSave();}return;}
  if(gesture.type==='ik'){const chain=project.ik_chains.find(x=>x.id===gesture.id);if(chain){renderer.solveIK(chain.id,point);gesture.last=point;dirty=true;scheduleSave();}return;}
  if(gesture.type==='bone'){const bone=project.bones.find(x=>x.id===gesture.id);if(bone){const dx=point.x-gesture.last.x,dy=point.y-gesture.last.y;bone.x+=dx;bone.y+=dy;renderer._boneCache.clear();gesture.last=point;dirty=true;scheduleSave();}}
});

function pointerEnd(event){pointers.delete(event.pointerId);if(pointers.size<2&&gesture?.type==='pinch')gesture=null;if(pointers.size===0){if(dirty)renderPanel();gesture=null;}}
canvas.addEventListener('pointerup',pointerEnd);canvas.addEventListener('pointercancel',pointerEnd);
canvas.addEventListener('wheel',event=>{event.preventDefault();renderer.zoomAt(Math.exp(-event.deltaY*.001),event.clientX,event.clientY);},{passive:false});

async function pollRuntimeState() {
  if(!runtimeMode||!token)return;
  try {
    const state=await api('/v1/avatar/state');renderer.setSemanticState(state);renderer.previewClipId=state.speaking&&project.animations.some(x=>x.id==='talk')?'talk':project.animations.some(x=>x.id==='idle')?'idle':null;connectionPill.textContent='live';connectionPill.classList.add('ok');
  } catch(_) {}
}

async function bootstrap() {
  if(!token){showPair();return;}
  try {
    project=await api('/v1/avatar/project');renderer.setProject(project);await loadAssets();renderer.fit();connectionPill.textContent='Core ok';connectionPill.classList.add('ok');hidePair();
    if(!runtimeMode){history=[JSON.stringify(project)];historyIndex=0;renderer.previewClipId=project.animations[0]?.id||null;renderPanel();}
    else {clearInterval(runtimePoll);await pollRuntimeState();runtimePoll=setInterval(pollRuntimeState,250);}
  } catch(error) {if(String(error.message)!=='pairing required')toast(`Core: ${error.message}`);}
}

function frame(now) {
  if(playing){const clip=activeClip();if(clip){renderer.previewTime += (now-lastFrame)/1000;if(renderer.previewTime>clip.duration)renderer.previewTime=clip.loop?renderer.previewTime%clip.duration:clip.duration;}}
  lastFrame=now;renderer.render(now);zoomLabel.textContent=`${Math.round(renderer.camera.scale*100)}%`;requestAnimationFrame(frame);
}

window.addEventListener('resize',()=>{renderer.resize();});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty)saveProject(false);});
window.addEventListener('beforeunload',()=>{if(dirty)navigator.sendBeacon?.('/health','');});

if('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(()=>{});
requestAnimationFrame(frame);
bootstrap();
