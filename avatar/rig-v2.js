// Amora Avatar Studio Rig V2 — additive socket rig + touch editing guards.
// Keeps V0.9 bone/IK support intact for segmented limbs.
// @ts-check
import { AvatarRenderer, clamp } from './engine.js';

const runtimeMode = new URLSearchParams(location.search).get('mode') === 'runtime';
const canvas = /** @type {HTMLCanvasElement|null} */ (document.querySelector('#stage'));
const original = {
  render: AvatarRenderer.prototype.render,
  partPose: AvatarRenderer.prototype._partPose,
  partWorld: AvatarRenderer.prototype._partWorld,
  setPivot: AvatarRenderer.prototype.setPartPivotAtWorld,
};

const v2 = {
  renderer: /** @type {AvatarRenderer|null} */ (null),
  tool: /** @type {null|'rotate'|'socket'} */ (null),
  anchorId: null,
  gesture: null,
  saveTimer: 0,
  migrated: new WeakSet(),
};

function rotatePoint(x, y, angle) {
  const c = Math.cos(angle); const s = Math.sin(angle);
  return { x: x * c - y * s, y: x * s + y * c };
}
function signedNonZero(value) {
  if (Math.abs(value) >= .0001) return value;
  return value < 0 ? -.0001 : .0001;
}
function idSafe(value) {
  const clean = String(value || '').normalize('NFKD').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 72);
  return clean || `socket_${Date.now()}`;
}
function uniqueId(base, list) {
  const used = new Set((list || []).map(item => item.id));
  if (!used.has(base)) return base;
  let i = 2; while (used.has(`${base}_${i}`)) i += 1;
  return `${base}_${i}`;
}
function toast(text) {
  const el = document.querySelector('#toast'); if (!el) return;
  el.textContent = text; el.classList.add('show');
  clearTimeout(el._v2Timer); el._v2Timer = setTimeout(() => el.classList.remove('show'), 1900);
}
function ensureV2(project) {
  if (!project || typeof project !== 'object') return;
  if (!Array.isArray(project.attachments)) project.attachments = [];
  for (const part of project.parts || []) {
    if (!Array.isArray(part.anchors)) part.anchors = [];
    if (typeof part.locked !== 'boolean') part.locked = false;
    if (typeof part.pickable !== 'boolean') part.pickable = true;
    if (typeof part.flip_x !== 'boolean') part.flip_x = false;
    if (typeof part.flip_y !== 'boolean') part.flip_y = false;
  }
  // Do not auto-save only because an old project was opened. The first V2 edit
  // upgrades the schema. This avoids surprising writes to carefully mapped V1 data.
  v2.migrated.add(project);
}
function project() { const p = v2.renderer?.project || null; if (p) ensureV2(p); return p; }
function selectedPart() {
  const p = project(); const id = v2.renderer?.selectedPartId;
  return p?.parts?.find(item => item.id === id) || null;
}
function activeAttachmentForChild(partId) {
  return (project()?.attachments || []).find(item => item.enabled !== false && item.child_part_id === partId) || null;
}
function selectedImage(part = selectedPart()) { return part && v2.renderer?.assets.get(part.asset_id) || null; }

function animationContext(renderer) {
  const elapsed = performance.now() / 1000;
  const animationTime = renderer.previewClipId ? (renderer.realtimeAnimation ? elapsed : renderer.previewTime) : elapsed;
  return { elapsed, overrides: renderer._animationOverrides(animationTime) };
}
function anchorById(part, anchorId) { return part?.anchors?.find(item => item.id === anchorId) || null; }
function localAnchorVector(part, pose, anchor, image) {
  if (!anchor || !image) return { x: 0, y: 0 };
  return {
    x: (anchor.x - pose.pivotX) * image.width * pose.scaleX,
    y: (anchor.y - pose.pivotY) * image.height * pose.scaleY,
  };
}
function socketPartWorld(renderer, part, pose, overrides, stack = new Set()) {
  const p = renderer.project; if (!p) return original.partWorld.call(renderer, part, pose, overrides);
  const attachment = (p.attachments || []).find(item => item.enabled !== false && item.child_part_id === part.id);
  if (!attachment) return original.partWorld.call(renderer, part, pose, overrides);
  if (stack.has(part.id)) return original.partWorld.call(renderer, part, pose, overrides);
  stack.add(part.id);
  const parent = p.parts.find(item => item.id === attachment.parent_part_id);
  const parentAnchor = anchorById(parent, attachment.parent_anchor_id);
  const childAnchor = anchorById(part, attachment.child_anchor_id);
  const parentImage = parent && renderer.assets.get(parent.asset_id);
  const childImage = renderer.assets.get(part.asset_id);
  if (!parent || !parentAnchor || !childAnchor || !parentImage || !childImage) {
    stack.delete(part.id); return original.partWorld.call(renderer, part, pose, overrides);
  }
  const parentPose = renderer._partPose(parent, overrides, performance.now() / 1000);
  const parentWorld = socketPartWorld(renderer, parent, parentPose, overrides, stack);
  const parentVector = localAnchorVector(parent, parentPose, parentAnchor, parentImage);
  const parentAnchorShift = rotatePoint(parentVector.x, parentVector.y, parentWorld.rotation);
  const anchorWorld = { x: parentWorld.x + parentAnchorShift.x, y: parentWorld.y + parentAnchorShift.y };
  const rotation = attachment.inherit_rotation !== false ? parentWorld.rotation + pose.rotation : pose.rotation;
  const childVector = localAnchorVector(part, pose, childAnchor, childImage);
  const childShift = rotatePoint(childVector.x, childVector.y, rotation);
  const offsetRotation = attachment.inherit_rotation !== false ? parentWorld.rotation : 0;
  const extra = rotatePoint(pose.x, pose.y, offsetRotation);
  stack.delete(part.id);
  return { x: anchorWorld.x + extra.x - childShift.x, y: anchorWorld.y + extra.y - childShift.y, rotation };
}
function anchorWorld(renderer, part, anchor, overrides = null) {
  const image = renderer.assets.get(part.asset_id); if (!image) return null;
  const context = overrides ? { elapsed: performance.now()/1000, overrides } : animationContext(renderer);
  const pose = renderer._partPose(part, context.overrides, context.elapsed);
  const world = socketPartWorld(renderer, part, pose, context.overrides, new Set());
  const vector = localAnchorVector(part, pose, anchor, image); const shift = rotatePoint(vector.x, vector.y, world.rotation);
  return { x: world.x + shift.x, y: world.y + shift.y, rotation: world.rotation, pose, world };
}
function pointInPart(renderer, part, point) {
  const image = renderer.assets.get(part.asset_id); if (!image || !part.visible || part.pickable === false || part.locked) return false;
  const {elapsed, overrides} = animationContext(renderer); renderer._boneCache.clear();
  const pose = renderer._partPose(part, overrides, elapsed); const world = renderer._partWorld(part, pose, overrides);
  const local = rotatePoint(point.x - world.x, point.y - world.y, -world.rotation);
  const left = -pose.pivotX * image.width * pose.scaleX; const top = -pose.pivotY * image.height * pose.scaleY;
  const right = left + image.width * pose.scaleX; const bottom = top + image.height * pose.scaleY;
  return local.x >= Math.min(left,right) && local.x <= Math.max(left,right) && local.y >= Math.min(top,bottom) && local.y <= Math.max(top,bottom);
}

// Compose explicit flip state with animation scale. Old projects keep exactly the
// same scale values until the user presses Flip H/V.
AvatarRenderer.prototype._partPose = function(part, overrides, elapsed) {
  const pose = original.partPose.call(this, part, overrides, elapsed);
  if (part.flip_x) pose.scaleX *= -1;
  if (part.flip_y) pose.scaleY *= -1;
  return pose;
};
AvatarRenderer.prototype._partWorld = function(part, pose, overrides) {
  return socketPartWorld(this, part, pose, overrides, new Set());
};

// Correct pivot editing with signed scales (flipped sprites) and selected sockets.
AvatarRenderer.prototype.setPartPivotAtWorld = function(partId, point, image) {
  if (!this.project || !image) return false;
  const part = this.project.parts.find(item => item.id === partId); if (!part) return false;
  ensureV2(this.project);
  const t = part.transform; const overrides = new Map(); this._boneCache.clear();
  const pose = this._partPose(part, overrides, performance.now()/1000); const world = this._partWorld(part, pose, overrides);
  const localFromPivot = rotatePoint(point.x - world.x, point.y - world.y, -world.rotation);
  const imageX = localFromPivot.x / signedNonZero(pose.scaleX) + pose.pivotX * image.width;
  const imageY = localFromPivot.y / signedNonZero(pose.scaleY) + pose.pivotY * image.height;
  const newPivotX = clamp(imageX / Math.max(1, image.width), 0, 1); const newPivotY = clamp(imageY / Math.max(1, image.height), 0, 1);
  const attached = activeAttachmentForChild(part.id);
  if (!attached) {
    const dx = (newPivotX - pose.pivotX) * image.width * pose.scaleX;
    const dy = (newPivotY - pose.pivotY) * image.height * pose.scaleY;
    const worldDelta = rotatePoint(dx, dy, world.rotation);
    const bone = part.bone_id ? this._boneWorld(part.bone_id, overrides, new Map()) : null;
    const localDelta = bone ? rotatePoint(worldDelta.x, worldDelta.y, -bone.rotation) : worldDelta;
    t.x += localDelta.x; t.y += localDelta.y;
  }
  t.pivot_x = newPivotX; t.pivot_y = newPivotY;
  return true;
};

// Respect the user's explicit selection before topmost hit-testing. Select remains
// topmost-first; Move/Pivot are intercepted below and therefore cannot steal layer.
AvatarRenderer.prototype.pickPart = function(point) {
  if (!this.project) return null; ensureV2(this.project);
  const selected = this.project.parts.find(item => item.id === this.selectedPartId);
  if (selected && pointInPart(this, selected, point)) return selected;
  const sorted = [...this.project.parts].sort((a,b) => b.layer - a.layer);
  for (const part of sorted) if (pointInPart(this, part, point)) return part;
  return null;
};

function drawSocketOverlay(renderer) {
  if (runtimeMode || !renderer.project) return;
  const part = renderer.project.parts.find(item => item.id === renderer.selectedPartId); if (!part) return;
  const ctx = renderer.ctx; const size = renderer.resize(); const dpr = size.dpr;
  const {overrides} = animationContext(renderer);
  ctx.save(); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.translate(renderer.camera.x, renderer.camera.y); ctx.scale(renderer.camera.scale, renderer.camera.scale);
  const line = 2 / Math.max(.05, renderer.camera.scale); const radius = 8 / Math.max(.05, renderer.camera.scale);
  for (const attachment of renderer.project.attachments || []) {
    if (attachment.enabled === false) continue;
    if (attachment.parent_part_id !== part.id && attachment.child_part_id !== part.id) continue;
    const parent = renderer.project.parts.find(item => item.id === attachment.parent_part_id);
    const child = renderer.project.parts.find(item => item.id === attachment.child_part_id);
    const pa = anchorById(parent, attachment.parent_anchor_id); const ca = anchorById(child, attachment.child_anchor_id);
    if (!parent || !child || !pa || !ca) continue;
    const p1 = anchorWorld(renderer,parent,pa,overrides); const p2 = anchorWorld(renderer,child,ca,overrides); if(!p1||!p2)continue;
    ctx.strokeStyle='rgba(126,231,255,.75)';ctx.lineWidth=line;ctx.setLineDash([8/renderer.camera.scale,5/renderer.camera.scale]);ctx.beginPath();ctx.moveTo(p1.x,p1.y);ctx.lineTo(p2.x,p2.y);ctx.stroke();ctx.setLineDash([]);
  }
  for (const anchor of part.anchors || []) {
    const point = anchorWorld(renderer, part, anchor, overrides); if (!point) continue;
    const selected = anchor.id === v2.anchorId;
    ctx.fillStyle = selected ? '#ffd27d' : '#77e7ff'; ctx.strokeStyle='#171228';ctx.lineWidth=line;
    ctx.beginPath();ctx.arc(point.x,point.y,radius,0,Math.PI*2);ctx.fill();ctx.stroke();
    ctx.font=`${12/Math.max(.45,renderer.camera.scale)}px system-ui`;ctx.fillStyle='#fff';ctx.fillText(anchor.name,point.x+radius*1.4,point.y-radius*.8);
  }
  ctx.restore();
}
AvatarRenderer.prototype.render = function(now) {
  v2.renderer = this; if (this.project) ensureV2(this.project);
  original.render.call(this, now); drawSocketOverlay(this);
};

async function saveV2() {
  const renderer=v2.renderer; const p=project(); if(!renderer||!p||runtimeMode)return;
  clearTimeout(v2.saveTimer); p.schema_version=Math.max(2,Number(p.schema_version)||1);
  const token=localStorage.getItem('amora.avatar.token')||'';
  try {
    const response=await fetch('/v1/avatar/project',{method:'PUT',headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify(p)});
    if(!response.ok){let detail=String(response.status);try{detail=(await response.json()).detail||detail;}catch(_){}throw new Error(detail);}
    const normalized=await response.json();
    // Preserve the object identity used by app.js while accepting server defaults.
    for(const key of Object.keys(p))delete p[key];Object.assign(p,normalized);ensureV2(p);renderer.setProject(p);
  } catch(error) { toast(`Rig v2 não salvou: ${error.message||error}`); }
}
function scheduleV2Save(){clearTimeout(v2.saveTimer);v2.saveTimer=setTimeout(saveV2,500);}
function changed(message){scheduleV2Save();injectPanels();if(message)toast(message);}

function addSocket(part) {
  ensureV2(project());
  const base=uniqueId('socket',part.anchors); part.anchors.push({id:base,name:`Socket ${part.anchors.length+1}`,x:.5,y:.5});v2.anchorId=base;v2.tool='socket';changed('Arraste o ponto para posicionar o socket');
}
function deleteSocket(part, anchorId) {
  const p=project(); if(!p)return;
  p.attachments=p.attachments.filter(item=>!((item.parent_part_id===part.id&&item.parent_anchor_id===anchorId)||(item.child_part_id===part.id&&item.child_anchor_id===anchorId)));
  part.anchors=part.anchors.filter(item=>item.id!==anchorId);if(v2.anchorId===anchorId)v2.anchorId=null;changed('Socket removido');
}
function connectSelected(parentPartId,parentAnchorId,childAnchorId) {
  const p=project();const child=selectedPart();if(!p||!child)return;
  const parent=p.parts.find(item=>item.id===parentPartId);if(!parent||parent.id===child.id)return toast('Escolha outra peça como parent');
  if(!anchorById(parent,parentAnchorId)||!anchorById(child,childAnchorId))return toast('Escolha sockets válidos');
  p.attachments=p.attachments.filter(item=>item.child_part_id!==child.id);
  child.transform.x=0;child.transform.y=0;
  p.attachments.push({id:uniqueId(`attach_${idSafe(parent.id)}_${idSafe(child.id)}`,p.attachments),name:`${parent.name} → ${child.name}`,parent_part_id:parent.id,parent_anchor_id:parentAnchorId,child_part_id:child.id,child_anchor_id:childAnchorId,inherit_rotation:true,enabled:true});
  changed('Peças conectadas');
}
function disconnectSelected(){const p=project();const part=selectedPart();if(!p||!part)return;p.attachments=p.attachments.filter(item=>item.child_part_id!==part.id);changed('Peça desconectada');}
function togglePartField(field){const part=selectedPart();if(!part)return;part[field]=!part[field];changed();}

function toolButton(label,tool,title){const b=document.createElement('button');b.type='button';b.className='v2-tool';b.dataset.v2Tool=tool;b.title=title;b.textContent=label;b.addEventListener('click',()=>{v2.tool=v2.tool===tool?null:tool;syncToolButtons();});return b;}
function installToolbar(){if(runtimeMode||document.querySelector('.v2-toolbar'))return;const hud=document.querySelector('.viewport-hud');if(!hud)return;const wrap=document.createElement('div');wrap.className='v2-toolbar';wrap.append(toolButton('⟳ Girar','rotate','Rotacionar peça selecionada'));wrap.append(toolButton('● Socket','socket','Posicionar socket selecionado'));hud.append(wrap);syncToolButtons();}
function syncToolButtons(){document.querySelectorAll('[data-v2-tool]').forEach(el=>el.classList.toggle('active',el.dataset.v2Tool===v2.tool));}

document.addEventListener('click',event=>{
  const oldTool=event.target.closest?.('[data-tool]');if(oldTool&&!event.target.closest?.('[data-v2-tool]')){v2.tool=null;syncToolButtons();}
  const del=event.target.closest?.('[data-act="delete-part"]');if(del){const p=project();const part=selectedPart();if(p&&part)p.attachments=p.attachments.filter(item=>item.parent_part_id!==part.id&&item.child_part_id!==part.id);}
},true);

function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function socketSectionHtml(part){
  const p=project();if(!p||!part)return'';const attached=activeAttachmentForChild(part.id);
  const parentOptions=p.parts.filter(x=>x.id!==part.id).map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
  const anchors=(part.anchors||[]).map(a=>`<div class="v2-anchor-row"><input data-v2-anchor-name="${esc(a.id)}" value="${esc(a.name)}"><button data-v2-place="${esc(a.id)}">◎</button><button data-v2-delete-anchor="${esc(a.id)}">×</button></div>`).join('');
  const childOptions=(part.anchors||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
  return `<div class="section v2-section"><div class="section-title">Rig v2 · peça rígida</div>
    <div class="row v2-actions"><button data-v2-flip-x class="${part.flip_x?'active':''}">↔ Flip H</button><button data-v2-flip-y class="${part.flip_y?'active':''}">↕ Flip V</button></div>
    <div class="row v2-actions"><button data-v2-lock>${part.locked?'🔒 Travada':'🔓 Livre'}</button><button data-v2-pick>${part.pickable===false?'☝ Click-through':'🎯 Selecionável'}</button></div>
    <small>Move/Pivô agora respeitam a peça escolhida na lista. “Selecionar” continua escolhendo pelo canvas.</small>
    <div class="section-title" style="margin-top:12px">Sockets</div>${anchors||'<small>Nenhum socket nesta peça.</small>'}<button data-v2-add-anchor>＋ Socket</button>
    ${attached?`<div class="v2-connected"><strong>Conectada:</strong> ${esc(attached.name)} <button data-v2-disconnect>Desconectar</button></div>`:''}
    ${(part.anchors||[]).length&&p.parts.length>1?`<div class="section-title" style="margin-top:12px">Conectar esta peça</div><div class="row"><label>Meu socket</label><select data-v2-child-anchor>${childOptions}</select></div><div class="row"><label>Parent</label><select data-v2-parent>${parentOptions}</select></div><div class="row"><label>Socket parent</label><select data-v2-parent-anchor></select></div><button data-v2-connect class="primary">Conectar / substituir</button>`:''}
    <small style="display:block;margin-top:8px">Braço inteiro usa sockets. Membros segmentados podem continuar usando Bones + IK normal.</small>
  </div>`;
}
function hydrateParentAnchors(root){const parentSel=root.querySelector('[data-v2-parent]');const anchorSel=root.querySelector('[data-v2-parent-anchor]');if(!parentSel||!anchorSel)return;const fill=()=>{const parent=project()?.parts.find(x=>x.id===parentSel.value);anchorSel.innerHTML=(parent?.anchors||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');};parentSel.addEventListener('change',fill);fill();}
function bindV2Section(root){
  if(root.dataset.v2Bound==='1')return;root.dataset.v2Bound='1';
  root.querySelector('[data-v2-flip-x]')?.addEventListener('click',()=>togglePartField('flip_x'));
  root.querySelector('[data-v2-flip-y]')?.addEventListener('click',()=>togglePartField('flip_y'));
  root.querySelector('[data-v2-lock]')?.addEventListener('click',()=>togglePartField('locked'));
  root.querySelector('[data-v2-pick]')?.addEventListener('click',()=>togglePartField('pickable'));
  root.querySelector('[data-v2-add-anchor]')?.addEventListener('click',()=>{const part=selectedPart();if(part)addSocket(part);});
  root.querySelectorAll('[data-v2-place]').forEach(btn=>btn.addEventListener('click',()=>{v2.anchorId=btn.dataset.v2Place;v2.tool='socket';syncToolButtons();toast('Arraste o socket no desenho');}));
  root.querySelectorAll('[data-v2-delete-anchor]').forEach(btn=>btn.addEventListener('click',()=>{const part=selectedPart();if(part)deleteSocket(part,btn.dataset.v2DeleteAnchor);}));
  root.querySelectorAll('[data-v2-anchor-name]').forEach(input=>input.addEventListener('change',()=>{const part=selectedPart();const anchor=part&&anchorById(part,input.dataset.v2AnchorName);if(anchor){anchor.name=input.value.trim()||anchor.name;changed();}}));
  root.querySelector('[data-v2-disconnect]')?.addEventListener('click',disconnectSelected);
  hydrateParentAnchors(root);
  root.querySelector('[data-v2-connect]')?.addEventListener('click',()=>{const parent=root.querySelector('[data-v2-parent]');const pa=root.querySelector('[data-v2-parent-anchor]');const ca=root.querySelector('[data-v2-child-anchor]');if(!parent||!pa||!ca)return;if(!pa.value)return toast('O parent precisa ter pelo menos um socket');connectSelected(parent.value,pa.value,ca.value);});
}
function injectInto(container){if(!container||runtimeMode)return;const part=selectedPart();const existing=container.querySelector('.v2-section');if(!part){existing?.remove();return;}if(existing)existing.remove();container.insertAdjacentHTML('beforeend',socketSectionHtml(part));const section=container.querySelector('.v2-section');if(section)bindV2Section(section);}
function injectPanels(){installToolbar();injectInto(document.querySelector('#sheetBody'));injectInto(document.querySelector('#desktopInspector'));}
const observer=new MutationObserver(()=>queueMicrotask(injectPanels));observer.observe(document.body,{childList:true,subtree:true});

let captureGesture=null;
function currentOldTool(){return document.querySelector('[data-tool].active')?.dataset?.tool||'select';}
function captureStart(event){
  if(runtimeMode||!v2.renderer||!project())return;const renderer=v2.renderer;const part=selectedPart();if(!part)return;
  const point=renderer.clientToProject(event.clientX,event.clientY);const oldTool=currentOldTool();
  const mode=v2.tool||((oldTool==='move'||oldTool==='pivot')?oldTool:null);if(!mode)return;
  if(part.locked){event.preventDefault();event.stopImmediatePropagation();toast('Layer travada');return;}
  if(mode==='move'&&activeAttachmentForChild(part.id)){event.preventDefault();event.stopImmediatePropagation();toast('Peça conectada: mova o parent ou os sockets');return;}
  if(mode==='socket'&&!v2.anchorId){event.preventDefault();event.stopImmediatePropagation();toast('Escolha ou crie um socket primeiro');return;}
  event.preventDefault();event.stopImmediatePropagation();canvas?.setPointerCapture(event.pointerId);
  const {overrides}=animationContext(renderer);const pose=renderer._partPose(part,overrides,performance.now()/1000);const world=renderer._partWorld(part,pose,overrides);
  if(mode==='rotate'){captureGesture={mode,id:part.id,lastAngle:Math.atan2(point.y-world.y,point.x-world.x),startRotation:part.transform.rotation};return;}
  captureGesture={mode,id:part.id,last:point};
  if(mode==='socket')placeSocketAt(point);
}
function placeSocketAt(point){const renderer=v2.renderer;const part=selectedPart();const image=selectedImage(part);const anchor=part&&anchorById(part,v2.anchorId);if(!renderer||!part||!image||!anchor)return;const {overrides}=animationContext(renderer);const pose=renderer._partPose(part,overrides,performance.now()/1000);const world=renderer._partWorld(part,pose,overrides);const local=rotatePoint(point.x-world.x,point.y-world.y,-world.rotation);anchor.x=clamp((local.x/signedNonZero(pose.scaleX)+pose.pivotX*image.width)/Math.max(1,image.width),0,1);anchor.y=clamp((local.y/signedNonZero(pose.scaleY)+pose.pivotY*image.height)/Math.max(1,image.height),0,1);scheduleV2Save();}
function captureMove(event){if(!captureGesture||!v2.renderer)return;event.preventDefault();event.stopImmediatePropagation();const renderer=v2.renderer;const part=project()?.parts.find(x=>x.id===captureGesture.id);if(!part)return;const point=renderer.clientToProject(event.clientX,event.clientY);
  if(captureGesture.mode==='move'){renderer.movePartWorldDelta(part.id,point.x-captureGesture.last.x,point.y-captureGesture.last.y);captureGesture.last=point;scheduleV2Save();return;}
  if(captureGesture.mode==='pivot'){const image=renderer.assets.get(part.asset_id);if(image){renderer.setPartPivotAtWorld(part.id,point,image);captureGesture.last=point;scheduleV2Save();}return;}
  if(captureGesture.mode==='socket'){placeSocketAt(point);captureGesture.last=point;return;}
  if(captureGesture.mode==='rotate'){const {overrides}=animationContext(renderer);const pose=renderer._partPose(part,overrides,performance.now()/1000);const world=renderer._partWorld(part,pose,overrides);const angle=Math.atan2(point.y-world.y,point.x-world.x);part.transform.rotation=captureGesture.startRotation+(angle-captureGesture.lastAngle);scheduleV2Save();}
}
function captureEnd(event){if(!captureGesture)return;event.preventDefault();event.stopImmediatePropagation();captureGesture=null;saveV2();injectPanels();}
canvas?.addEventListener('pointerdown',captureStart,true);canvas?.addEventListener('pointermove',captureMove,true);canvas?.addEventListener('pointerup',captureEnd,true);canvas?.addEventListener('pointercancel',captureEnd,true);

// V2 micro styles are kept here so the existing stylesheet remains backward-compatible.
const style=document.createElement('style');style.textContent=`
.v2-toolbar{display:inline-flex;gap:6px;margin-left:6px}.v2-toolbar button{min-height:34px}.v2-toolbar button.active,.v2-actions button.active{outline:2px solid #d8a9ff;background:#50326b}.v2-section{border-top:1px solid rgba(255,255,255,.12)}.v2-anchor-row{display:grid;grid-template-columns:1fr 42px 42px;gap:6px;margin:6px 0}.v2-anchor-row input{min-width:0}.v2-actions{gap:6px}.v2-connected{margin:10px 0;padding:8px;border-radius:10px;background:rgba(119,231,255,.08)}
`;document.head.append(style);

setInterval(()=>{if(v2.renderer?.project){ensureV2(v2.renderer.project);injectPanels();}},700);
