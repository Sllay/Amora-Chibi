// Amora Avatar Engine V0.9 — dependency-free Canvas2D runtime shared by Studio and Runtime.
// @ts-check

const TAU = Math.PI * 2;

export function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
export function lerp(a, b, t) { return a + (b - a) * t; }

function ease(name, t) {
  t = clamp(t, 0, 1);
  if (name === 'linear') return t;
  if (name === 'ease_in') return t * t;
  if (name === 'ease_out') return 1 - (1 - t) * (1 - t);
  if (name === 'spring') return 1 - Math.cos(t * Math.PI * 4) * Math.exp(-5 * t);
  return t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

function sampleKeyframes(keyframes, time) {
  if (!keyframes?.length) return undefined;
  const sorted = [...keyframes].sort((a, b) => a.time - b.time);
  if (time <= sorted[0].time) return sorted[0].value;
  if (time >= sorted.at(-1).time) return sorted.at(-1).value;
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const a = sorted[i]; const b = sorted[i + 1];
    if (time < a.time || time > b.time) continue;
    const span = Math.max(.00001, b.time - a.time);
    const t = ease(b.easing || 'ease_in_out', (time - a.time) / span);
    if (typeof a.value === 'number' && typeof b.value === 'number') return lerp(a.value, b.value, t);
    if (Array.isArray(a.value) && Array.isArray(b.value)) return a.value.map((v, n) => lerp(Number(v), Number(b.value[n] ?? v), t));
    return t < .5 ? a.value : b.value;
  }
  return sorted.at(-1).value;
}

function safeColor(value) { return /^#[0-9a-f]{6}$/i.test(value || '') ? value : '#ffffff'; }
function rotatePoint(x, y, angle) {
  const c = Math.cos(angle); const s = Math.sin(angle);
  return { x: x * c - y * s, y: x * s + y * c };
}

export class AvatarRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: true, desynchronized: true });
    this.project = null;
    this.assets = new Map();
    this.tintCache = new Map();
    this.semanticState = null;
    this.expressionOverrideId = null;
    this.camera = { scale: 1, x: 0, y: 0 };
    this.showGrid = false;
    this.showRig = false;
    this.showIK = false;
    this.selectedPartId = null;
    this.selectedBoneId = null;
    this.previewClipId = null;
    this.previewTime = 0;
    this.realtimeAnimation = false;
    this.proceduralEnabled = false;
    this.physics = new Map();
    this._lastFrame = performance.now();
    this._boneCache = new Map();
  }

  setProject(project) { this.project = project; this.tintCache.clear(); this._boneCache.clear(); }
  setAssets(assets) { this.assets = assets; this.tintCache.clear(); }
  setSemanticState(state) { this.semanticState = state; }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const width = Math.max(1, Math.round(rect.width * dpr)); const height = Math.max(1, Math.round(rect.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) { this.canvas.width = width; this.canvas.height = height; }
    return { width: rect.width, height: rect.height, dpr };
  }

  fit() {
    if (!this.project) return;
    const rect = this.canvas.getBoundingClientRect(); const pad = 34;
    const scale = Math.min((rect.width - pad * 2) / this.project.canvas.width, (rect.height - pad * 2) / this.project.canvas.height);
    this.camera.scale = clamp(scale, .08, 5);
    this.camera.x = (rect.width - this.project.canvas.width * this.camera.scale) / 2;
    this.camera.y = (rect.height - this.project.canvas.height * this.camera.scale) / 2;
  }

  clientToProject(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: (clientX - rect.left - this.camera.x) / this.camera.scale, y: (clientY - rect.top - this.camera.y) / this.camera.scale };
  }
  projectToClient(x, y) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: rect.left + this.camera.x + x * this.camera.scale, y: rect.top + this.camera.y + y * this.camera.scale };
  }
  pan(dx, dy) { this.camera.x += dx; this.camera.y += dy; }
  zoomAt(factor, clientX, clientY) {
    const before = this.clientToProject(clientX, clientY); const rect = this.canvas.getBoundingClientRect();
    this.camera.scale = clamp(this.camera.scale * factor, .05, 8);
    this.camera.x = clientX - rect.left - before.x * this.camera.scale;
    this.camera.y = clientY - rect.top - before.y * this.camera.scale;
  }

  colorSlots() { return new Map((this.project?.color_slots || []).map(slot => [slot.id, slot.value])); }
  expressionPreset() {
    if (!this.project) return null;
    const id = this.expressionOverrideId || this.semanticState?.expression || 'neutral';
    return this.project.expressions.find(item => item.id === id) || null;
  }

  _animationOverrides(timeSeconds) {
    const result = new Map();
    if (!this.project || !this.previewClipId) return result;
    const clip = this.project.animations.find(item => item.id === this.previewClipId); if (!clip) return result;
    const t = clip.loop ? (timeSeconds % clip.duration) : clamp(timeSeconds, 0, clip.duration);
    for (const track of clip.tracks || []) {
      const value = sampleKeyframes(track.keyframes, t); if (value === undefined) continue;
      if (!result.has(track.target_id)) result.set(track.target_id, new Map());
      result.get(track.target_id).set(track.property, value);
    }
    return result;
  }
  _override(overrides, target, property, fallback) { return overrides.get(target)?.get(property) ?? fallback; }

  _boneWorld(boneId, overrides, cache = this._boneCache) {
    if (!boneId || !this.project) return null;
    const cached = cache.get(boneId); if (cached) return cached;
    const bone = this.project.bones.find(item => item.id === boneId); if (!bone) return null;
    const parent = bone.parent_id ? this._boneWorld(bone.parent_id, overrides, cache) : null;
    const x = Number(this._override(overrides, bone.id, 'x', bone.x)); const y = Number(this._override(overrides, bone.id, 'y', bone.y));
    const localRotation = Number(this._override(overrides, bone.id, 'rotation', bone.rotation));
    let origin; let rotation;
    if (parent) { const shifted = rotatePoint(x, y, parent.rotation); origin = { x: parent.x + shifted.x, y: parent.y + shifted.y }; rotation = parent.rotation + localRotation; }
    else { origin = { x, y }; rotation = localRotation; }
    const length = Number(this._override(overrides, bone.id, 'length', bone.length));
    const world = { id: bone.id, x: origin.x, y: origin.y, rotation, length, endX: origin.x + Math.cos(rotation) * length, endY: origin.y + Math.sin(rotation) * length };
    cache.set(boneId, world); return world;
  }

  moveBoneWorldDelta(boneId, dx, dy) {
    if (!this.project) return false;
    const bone = this.project.bones.find(item => item.id === boneId); if (!bone) return false;
    this._boneCache.clear();
    const parent = bone.parent_id ? this._boneWorld(bone.parent_id, new Map(), new Map()) : null;
    const local = parent ? rotatePoint(dx, dy, -parent.rotation) : { x: dx, y: dy };
    bone.x += local.x; bone.y += local.y; this._boneCache.clear(); return true;
  }

  movePartWorldDelta(partId, dx, dy) {
    if (!this.project) return false;
    const part = this.project.parts.find(item => item.id === partId); if (!part) return false;
    this._boneCache.clear();
    const bone = part.bone_id ? this._boneWorld(part.bone_id, new Map(), new Map()) : null;
    const local = bone ? rotatePoint(dx, dy, -bone.rotation) : { x: dx, y: dy };
    part.transform.x += local.x; part.transform.y += local.y; return true;
  }

  reparentPartPreserveWorld(partId, newBoneId) {
    if (!this.project) return false;
    const part = this.project.parts.find(item => item.id === partId); if (!part) return false;
    const t = part.transform;
    this._boneCache.clear();
    const oldPose = { x:t.x, y:t.y, rotation:t.rotation, scaleX:t.scale_x, scaleY:t.scale_y, pivotX:t.pivot_x, pivotY:t.pivot_y, opacity:t.opacity, visible:t.visible };
    const oldWorld = this._partWorld(part, oldPose, new Map());
    part.bone_id = newBoneId || null; this._boneCache.clear();
    const bone = part.bone_id ? this._boneWorld(part.bone_id, new Map(), new Map()) : null;
    if (bone) {
      const local = rotatePoint(oldWorld.x - bone.x, oldWorld.y - bone.y, -bone.rotation);
      t.x = local.x; t.y = local.y; t.rotation = oldWorld.rotation - bone.rotation;
    } else { t.x = oldWorld.x; t.y = oldWorld.y; t.rotation = oldWorld.rotation; }
    return true;
  }

  setPartPivotAtWorld(partId, point, image) {
    if (!this.project || !image) return false;
    const part = this.project.parts.find(item => item.id === partId); if (!part) return false;
    const t = part.transform; const overrides = new Map(); this._boneCache.clear();
    const pose = { x:t.x, y:t.y, rotation:t.rotation, scaleX:t.scale_x, scaleY:t.scale_y, pivotX:t.pivot_x, pivotY:t.pivot_y, opacity:t.opacity, visible:t.visible };
    const world = this._partWorld(part, pose, overrides);
    const localFromPivot = rotatePoint(point.x - world.x, point.y - world.y, -world.rotation);
    const imageX = localFromPivot.x / Math.max(.0001, pose.scaleX) + pose.pivotX * image.width;
    const imageY = localFromPivot.y / Math.max(.0001, pose.scaleY) + pose.pivotY * image.height;
    const newPivotX = clamp(imageX / Math.max(1, image.width), 0, 1); const newPivotY = clamp(imageY / Math.max(1, image.height), 0, 1);
    const deltaWorld = { x: point.x - world.x, y: point.y - world.y };
    const bone = part.bone_id ? this._boneWorld(part.bone_id, overrides, new Map()) : null;
    const localDelta = bone ? rotatePoint(deltaWorld.x, deltaWorld.y, -bone.rotation) : deltaWorld;
    t.x += localDelta.x; t.y += localDelta.y; t.pivot_x = newPivotX; t.pivot_y = newPivotY;
    return true;
  }

  solveIK(chainId, target) {
    if (!this.project) return false;
    const chain = this.project.ik_chains.find(item => item.id === chainId); if (!chain) return false;
    const root = this.project.bones.find(item => item.id === chain.root_bone_id); const joint = this.project.bones.find(item => item.id === chain.joint_bone_id);
    if (!root || !joint) return false;
    this._boneCache.clear(); const rootWorld = this._boneWorld(root.id, new Map(), new Map()); if (!rootWorld) return false;
    const parentWorld = root.parent_id ? this._boneWorld(root.parent_id, new Map(), new Map()) : null; const parentRotation = parentWorld?.rotation || 0;
    const dx = target.x - rootWorld.x; const dy = target.y - rootWorld.y; const l1 = Math.max(.001, root.length); const l2 = Math.max(.001, joint.length);
    const distance = clamp(Math.hypot(dx, dy), Math.abs(l1 - l2) + .001, l1 + l2 - .001); const base = Math.atan2(dy, dx);
    const shoulderOffset = Math.acos(clamp((l1*l1 + distance*distance - l2*l2) / (2*l1*distance), -1, 1));
    const elbowInner = Math.acos(clamp((l1*l1 + l2*l2 - distance*distance) / (2*l1*l2), -1, 1));
    const sign = chain.bend === 'negative' ? -1 : 1; const shoulderWorld = base - sign * shoulderOffset; const elbowRelative = sign * (Math.PI - elbowInner);
    root.rotation = clamp(shoulderWorld - parentRotation, root.min_rotation, root.max_rotation); joint.rotation = clamp(elbowRelative, joint.min_rotation, joint.max_rotation);
    chain.target = { x: target.x, y: target.y }; this._boneCache.clear(); return true;
  }

  _blinkAmount(elapsed) {
    if (!this.proceduralEnabled || this.semanticState?.blink_allowed === false) return 0;
    const cycle = Number(this.project?.metadata?.blink_cycle_seconds || 4.15); const duration = .24; const phase = elapsed % Math.max(.8, cycle);
    if (phase < cycle - duration) return 0;
    const t = (phase - (cycle - duration)) / duration;
    return t < .5 ? t * 2 : (1 - t) * 2;
  }

  _partPose(part, overrides, elapsed) {
    const t = part.transform;
    const pose = {
      x:Number(this._override(overrides, part.id, 'transform.x', t.x)), y:Number(this._override(overrides, part.id, 'transform.y', t.y)),
      rotation:Number(this._override(overrides, part.id, 'transform.rotation', t.rotation)), scaleX:Number(this._override(overrides, part.id, 'transform.scale_x', t.scale_x)),
      scaleY:Number(this._override(overrides, part.id, 'transform.scale_y', t.scale_y)), pivotX:t.pivot_x, pivotY:t.pivot_y,
      opacity:Number(this._override(overrides, part.id, 'transform.opacity', t.opacity)), visible:Boolean(this._override(overrides, part.id, 'visible', part.visible)),
    };
    const preset = this.expressionPreset();
    if (preset?.visibility && Object.hasOwn(preset.visibility, part.id)) pose.visible = Boolean(preset.visibility[part.id]);
    if (preset?.part_opacity && Object.hasOwn(preset.part_opacity, part.id)) pose.opacity *= Number(preset.part_opacity[part.id]);
    const tags = new Set(part.tags || []); const speaking = Boolean(this.semanticState?.speaking); const mouthTag = [...tags].find(tag => tag.startsWith('mouth:'));
    if (mouthTag) {
      const selectedMouth = preset?.mouth_part_id || null; const isTalk = tags.has('mouth:talk'); const isNeutral = tags.has('mouth:neutral');
      if (speaking) {
        const open = Math.sin(elapsed * 32) > -.1;
        if (isTalk) pose.visible = open;
        else if (selectedMouth) pose.visible = !open && part.id === selectedMouth;
        else if (isNeutral) pose.visible = !open;
        else pose.visible = false;
      } else if (selectedMouth) pose.visible = part.id === selectedMouth;
      else if (isTalk) pose.visible = false;
    }
    if ([...tags].some(tag => tag.startsWith('brow:')) && preset?.brow_part_id) pose.visible = part.id === preset.brow_part_id;
    if (tags.has('blush')) {
      const semantic = Number(this.semanticState?.blush || 0); const expression = Number(preset?.blush_opacity || 0);
      pose.opacity *= clamp(Math.max(semantic, expression), 0, 1);
    }
    const blink = this._blinkAmount(elapsed); const blinkPx = Number(this.project?.metadata?.blink_pixels || 8);
    if (tags.has('eye:pupil')) pose.opacity *= 1 - blink;
    if (tags.has('eyelash:top')) pose.y += blinkPx * blink;
    if (tags.has('eyelash:bottom')) pose.y -= blinkPx * .65 * blink;

    const physics = this.project.physics?.find(slot => slot.target_part_id === part.id && slot.enabled);
    if (physics) {
      const state = this.physics.get(physics.id) || { angle:0, velocity:0 }; const target = Math.sin(elapsed*1.55 + part.layer*.13) * physics.gravity * .04;
      state.velocity += (target - state.angle) * physics.stiffness; state.velocity *= physics.damping; state.angle = clamp(state.angle + state.velocity, -physics.max_angle, physics.max_angle);
      this.physics.set(physics.id, state); pose.rotation += state.angle;
    }
    if (this.proceduralEnabled) {
      const arousal = Math.max(0, this.semanticState?.arousal || 0);
      if (tags.has('body')) pose.y += Math.sin(elapsed*2.1) * (1.2 + arousal*1.8);
      if (tags.has('head')) {
        pose.y += Math.sin(elapsed*2.1 + .4) * .9;
        if (this.semanticState?.gesture === 'curious_tilt') pose.rotation -= .10;
        if (this.semanticState?.gesture === 'attention') pose.rotation += Math.sin(elapsed*3.2) * .025;
        if (this.semanticState?.gesture === 'look_away') pose.rotation -= .07;
      }
    }
    return pose;
  }

  _partWorld(part, pose, overrides) {
    const bone = part.bone_id ? this._boneWorld(part.bone_id, overrides) : null;
    if (!bone) return { x:pose.x, y:pose.y, rotation:pose.rotation };
    const shifted = rotatePoint(pose.x, pose.y, bone.rotation);
    return { x:bone.x + shifted.x, y:bone.y + shifted.y, rotation:bone.rotation + pose.rotation };
  }

  _tinted(assetId, image, color) {
    const safe = safeColor(color); if (safe.toLowerCase() === '#ffffff') return image;
    const key = `${assetId}:${safe}`; const existing = this.tintCache.get(key); if (existing) return existing;
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height; const ctx = canvas.getContext('2d');
    ctx.clearRect(0,0,canvas.width,canvas.height); ctx.drawImage(image,0,0); ctx.globalCompositeOperation='multiply'; ctx.fillStyle=safe; ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.globalCompositeOperation='destination-in'; ctx.drawImage(image,0,0); ctx.globalCompositeOperation='source-over'; this.tintCache.set(key,canvas); return canvas;
  }

  pickPart(point) {
    if (!this.project) return null;
    const animationTime = this.realtimeAnimation ? performance.now()/1000 : this.previewTime; const overrides = this._animationOverrides(animationTime); this._boneCache.clear();
    const sorted = [...this.project.parts].sort((a,b)=>b.layer-a.layer);
    for (const part of sorted) {
      const image = this.assets.get(part.asset_id); if (!image || !part.visible) continue;
      const pose=this._partPose(part,overrides,performance.now()/1000); const world=this._partWorld(part,pose,overrides);
      const local=rotatePoint(point.x-world.x,point.y-world.y,-world.rotation); const left=-pose.pivotX*image.width*pose.scaleX; const top=-pose.pivotY*image.height*pose.scaleY;
      const right=left+image.width*pose.scaleX; const bottom=top+image.height*pose.scaleY;
      if(local.x>=Math.min(left,right)&&local.x<=Math.max(left,right)&&local.y>=Math.min(top,bottom)&&local.y<=Math.max(top,bottom)) return part;
    }
    return null;
  }

  render(now = performance.now()) {
    const size=this.resize(); const ctx=this.ctx; const dpr=size.dpr; ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,size.width,size.height); if(!this.project)return;
    this._lastFrame=now; const elapsed=now/1000; const animationTime=this.previewClipId?(this.realtimeAnimation?elapsed:this.previewTime):elapsed; const overrides=this._animationOverrides(animationTime); this._boneCache.clear();
    ctx.save(); ctx.translate(this.camera.x,this.camera.y); ctx.scale(this.camera.scale,this.camera.scale); ctx.fillStyle=this.project.canvas.background||'#171228'; ctx.fillRect(0,0,this.project.canvas.width,this.project.canvas.height);
    if(this.showGrid)this._drawGrid(ctx); const colors=this.colorSlots(); const parts=[...this.project.parts].sort((a,b)=>a.layer-b.layer);
    for(const part of parts){const image=this.assets.get(part.asset_id);if(!image)continue;const pose=this._partPose(part,overrides,elapsed);if(!pose.visible||pose.opacity<=.001)continue;const world=this._partWorld(part,pose,overrides);const color=part.tint_slot?colors.get(part.tint_slot)||'#ffffff':'#ffffff';const drawable=this._tinted(part.asset_id,image,color);ctx.save();ctx.globalAlpha=clamp(pose.opacity,0,1);ctx.translate(world.x,world.y);ctx.rotate(world.rotation);ctx.scale(pose.scaleX,pose.scaleY);ctx.drawImage(drawable,-pose.pivotX*image.width,-pose.pivotY*image.height);if(part.id===this.selectedPartId){ctx.globalAlpha=1;ctx.lineWidth=2/Math.max(.05,this.camera.scale);ctx.strokeStyle='#d9b8ff';ctx.strokeRect(-pose.pivotX*image.width,-pose.pivotY*image.height,image.width,image.height);ctx.fillStyle='#fff';const r=5/Math.max(.05,this.camera.scale);ctx.beginPath();ctx.arc(0,0,r,0,TAU);ctx.fill();}ctx.restore();}
    if(this.showRig||this.showIK)this._drawRig(ctx,overrides);ctx.restore();
  }

  _drawGrid(ctx){const step=50;ctx.save();ctx.strokeStyle='rgba(255,255,255,.07)';ctx.lineWidth=1/Math.max(.05,this.camera.scale);for(let x=0;x<=this.project.canvas.width;x+=step){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,this.project.canvas.height);ctx.stroke();}for(let y=0;y<=this.project.canvas.height;y+=step){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(this.project.canvas.width,y);ctx.stroke();}ctx.restore();}
  _drawRig(ctx,overrides){this._boneCache.clear();ctx.save();ctx.lineCap='round';for(const bone of this.project.bones||[]){const world=this._boneWorld(bone.id,overrides);if(!world)continue;ctx.strokeStyle=bone.id===this.selectedBoneId?'#ffcf75':'rgba(141,224,255,.9)';ctx.fillStyle=bone.id===this.selectedBoneId?'#ffcf75':'#8de0ff';ctx.lineWidth=3/Math.max(.05,this.camera.scale);ctx.beginPath();ctx.moveTo(world.x,world.y);ctx.lineTo(world.endX,world.endY);ctx.stroke();const radius=5/Math.max(.05,this.camera.scale);ctx.beginPath();ctx.arc(world.x,world.y,radius,0,TAU);ctx.fill();}if(this.showIK){for(const chain of this.project.ik_chains||[]){if(!chain.enabled)continue;ctx.strokeStyle='rgba(255,180,240,.65)';ctx.fillStyle='#ffb4f0';ctx.lineWidth=2/Math.max(.05,this.camera.scale);const root=this._boneWorld(chain.root_bone_id,overrides);if(root){ctx.setLineDash([6/this.camera.scale,5/this.camera.scale]);ctx.beginPath();ctx.moveTo(root.x,root.y);ctx.lineTo(chain.target.x,chain.target.y);ctx.stroke();ctx.setLineDash([]);}const r=8/Math.max(.05,this.camera.scale);ctx.beginPath();ctx.arc(chain.target.x,chain.target.y,r,0,TAU);ctx.stroke();}}ctx.restore();}
}

export function humanoidTemplate(width=720,height=1280){const cx=width/2;const top=height*.23;const torso=height*.18;const arm=width*.13;const forearm=width*.12;const thigh=height*.12;const shin=height*.12;return[
{id:'root',name:'Root',parent_id:null,x:cx,y:top+torso*2,rotation:-Math.PI/2,length:torso*.55,min_rotation:-Math.PI,max_rotation:Math.PI},
{id:'torso',name:'Torso',parent_id:'root',x:torso*.55,y:0,rotation:0,length:torso,min_rotation:-.45,max_rotation:.45},
{id:'neck',name:'Pescoço',parent_id:'torso',x:torso,y:0,rotation:0,length:height*.035,min_rotation:-.35,max_rotation:.35},
{id:'head',name:'Cabeça',parent_id:'neck',x:height*.035,y:0,rotation:0,length:height*.07,min_rotation:-.5,max_rotation:.5},
{id:'upper_arm_l',name:'Braço E',parent_id:'torso',x:torso*.78,y:-width*.045,rotation:Math.PI*.72,length:arm,min_rotation:-2.8,max_rotation:2.8},
{id:'forearm_l',name:'Antebraço E',parent_id:'upper_arm_l',x:arm,y:0,rotation:-.35,length:forearm,min_rotation:-2.7,max_rotation:2.7},
{id:'hand_l',name:'Mão E',parent_id:'forearm_l',x:forearm,y:0,rotation:0,length:width*.055,min_rotation:-1.3,max_rotation:1.3},
{id:'upper_arm_r',name:'Braço D',parent_id:'torso',x:torso*.78,y:width*.045,rotation:-Math.PI*.72,length:arm,min_rotation:-2.8,max_rotation:2.8},
{id:'forearm_r',name:'Antebraço D',parent_id:'upper_arm_r',x:arm,y:0,rotation:.35,length:forearm,min_rotation:-2.7,max_rotation:2.7},
{id:'hand_r',name:'Mão D',parent_id:'forearm_r',x:forearm,y:0,rotation:0,length:width*.055,min_rotation:-1.3,max_rotation:1.3},
{id:'thigh_l',name:'Coxa E',parent_id:'root',x:0,y:-width*.04,rotation:-Math.PI*.1,length:thigh,min_rotation:-1.8,max_rotation:1.2},
{id:'shin_l',name:'Canela E',parent_id:'thigh_l',x:thigh,y:0,rotation:.2,length:shin,min_rotation:-2.2,max_rotation:.6},
{id:'foot_l',name:'Pé E',parent_id:'shin_l',x:shin,y:0,rotation:Math.PI/2,length:width*.07,min_rotation:-.8,max_rotation:.8},
{id:'thigh_r',name:'Coxa D',parent_id:'root',x:0,y:width*.04,rotation:Math.PI*.1,length:thigh,min_rotation:-1.2,max_rotation:1.8},
{id:'shin_r',name:'Canela D',parent_id:'thigh_r',x:thigh,y:0,rotation:-.2,length:shin,min_rotation:-.6,max_rotation:2.2},
{id:'foot_r',name:'Pé D',parent_id:'shin_r',x:shin,y:0,rotation:Math.PI/2,length:width*.07,min_rotation:-.8,max_rotation:.8}];}

export function standardIK(project){const find=id=>project.bones.some(b=>b.id===id);const chains=[];if(find('upper_arm_l')&&find('forearm_l')&&find('hand_l'))chains.push({id:'arm_l',name:'Braço E',root_bone_id:'upper_arm_l',joint_bone_id:'forearm_l',end_bone_id:'hand_l',target:{x:project.canvas.width*.25,y:project.canvas.height*.55},bend:'positive',enabled:true});if(find('upper_arm_r')&&find('forearm_r')&&find('hand_r'))chains.push({id:'arm_r',name:'Braço D',root_bone_id:'upper_arm_r',joint_bone_id:'forearm_r',end_bone_id:'hand_r',target:{x:project.canvas.width*.75,y:project.canvas.height*.55},bend:'negative',enabled:true});if(find('thigh_l')&&find('shin_l')&&find('foot_l'))chains.push({id:'leg_l',name:'Perna E',root_bone_id:'thigh_l',joint_bone_id:'shin_l',end_bone_id:'foot_l',target:{x:project.canvas.width*.43,y:project.canvas.height*.86},bend:'positive',enabled:true});if(find('thigh_r')&&find('shin_r')&&find('foot_r'))chains.push({id:'leg_r',name:'Perna D',root_bone_id:'thigh_r',joint_bone_id:'shin_r',end_bone_id:'foot_r',target:{x:project.canvas.width*.57,y:project.canvas.height*.86},bend:'negative',enabled:true});return chains;}
