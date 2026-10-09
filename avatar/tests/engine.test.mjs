import test from 'node:test';
import assert from 'node:assert/strict';

import { AvatarRenderer, humanoidTemplate, standardIK } from '../engine.js';

function project() {
  const bones = humanoidTemplate(720, 1280);
  const value = {
    canvas: { width: 720, height: 1280, background: '#000000' },
    bones,
    ik_chains: [],
    parts: [],
    color_slots: [],
    expressions: [],
    animations: [],
    physics: [],
    metadata: {},
  };
  value.ik_chains = standardIK(value);
  return value;
}

function rendererFor(value) {
  const fakeCanvas = { getContext: () => ({}) };
  const renderer = new AvatarRenderer(fakeCanvas);
  renderer.setProject(value);
  return renderer;
}

function transform(x = 0, y = 0) {
  return { x, y, rotation: 0, scale_x: 1, scale_y: 1, pivot_x: .5, pivot_y: .5, opacity: 1 };
}

function worldOf(renderer, part) {
  renderer._boneCache.clear();
  const t = part.transform;
  return renderer._partWorld(part, {
    x: t.x, y: t.y, rotation: t.rotation,
    scaleX: t.scale_x, scaleY: t.scale_y,
    pivotX: t.pivot_x, pivotY: t.pivot_y,
    opacity: t.opacity, visible: part.visible,
  }, new Map());
}

test('humanoid template has unique hierarchy ids and four standard IK chains', () => {
  const value = project();
  assert.equal(value.bones.length, 16);
  assert.equal(new Set(value.bones.map(item => item.id)).size, value.bones.length);
  assert.deepEqual(value.ik_chains.map(item => item.id), ['arm_l', 'arm_r', 'leg_l', 'leg_r']);
  for (const chain of value.ik_chains) {
    assert.ok(value.bones.some(item => item.id === chain.root_bone_id));
    assert.ok(value.bones.some(item => item.id === chain.joint_bone_id));
    assert.ok(value.bones.some(item => item.id === chain.end_bone_id));
  }
});

test('two-bone IK moves end effector to reachable target', () => {
  const value = project();
  const renderer = rendererFor(value);
  const chain = value.ik_chains.find(item => item.id === 'arm_l');
  assert.ok(chain);

  renderer._boneCache.clear();
  const rootBefore = renderer._boneWorld(chain.root_bone_id, new Map(), new Map());
  assert.ok(rootBefore);
  const rootBone = value.bones.find(item => item.id === chain.root_bone_id);
  const jointBone = value.bones.find(item => item.id === chain.joint_bone_id);
  const reachable = Math.min(rootBone.length + jointBone.length - 2, 120);
  const target = { x: rootBefore.x + reachable * .8, y: rootBefore.y + reachable * .35 };

  assert.equal(renderer.solveIK(chain.id, target), true);
  renderer._boneCache.clear();
  const end = renderer._boneWorld(chain.end_bone_id, new Map(), new Map());
  assert.ok(end);
  assert.ok(Math.hypot(end.x - target.x, end.y - target.y) < 1.5, `end=${end.x},${end.y} target=${target.x},${target.y}`);
});

test('reparenting a sprite to a rotated bone preserves its world pose', () => {
  const value = project();
  const part = {
    id: 'face', name: 'Face', asset_id: 'face', layer: 1,
    bone_id: null, tint_slot: 'skin', visible: true,
    transform: { ...transform(330, 260), rotation: .17 }, tags: ['head'],
  };
  value.parts.push(part);
  const renderer = rendererFor(value);
  const before = worldOf(renderer, part);

  assert.equal(renderer.reparentPartPreserveWorld('face', 'head'), true);
  const after = worldOf(renderer, part);
  assert.ok(Math.hypot(after.x - before.x, after.y - before.y) < 1e-6);
  assert.ok(Math.abs(after.rotation - before.rotation) < 1e-6);
});

test('moving a bone-bound sprite by world delta follows the finger direction', () => {
  const value = project();
  const part = {
    id: 'hand_sprite', name: 'Mão', asset_id: 'hand', layer: 1,
    bone_id: 'hand_l', tint_slot: 'skin', visible: true,
    transform: transform(4, -3), tags: [],
  };
  value.parts.push(part);
  const renderer = rendererFor(value);
  const before = worldOf(renderer, part);
  assert.equal(renderer.movePartWorldDelta(part.id, 31, -14), true);
  const after = worldOf(renderer, part);
  assert.ok(Math.abs((after.x - before.x) - 31) < 1e-6);
  assert.ok(Math.abs((after.y - before.y) + 14) < 1e-6);
});

test('expression mouth selection yields to talk mouth while speaking', () => {
  const value = project();
  const neutral = { id:'mouth_neutral', name:'Neutra', asset_id:'n', layer:1, bone_id:null, tint_slot:null, visible:true, transform:transform(), tags:['mouth:neutral'] };
  const angry = { id:'mouth_angry', name:'Raiva', asset_id:'a', layer:2, bone_id:null, tint_slot:null, visible:true, transform:transform(), tags:['mouth:angry'] };
  const talk = { id:'mouth_talk', name:'Falando', asset_id:'t', layer:3, bone_id:null, tint_slot:null, visible:true, transform:transform(), tags:['mouth:talk'] };
  value.parts.push(neutral, angry, talk);
  value.expressions.push({ id:'angry', name:'Raiva', visibility:{}, part_opacity:{}, mouth_part_id:'mouth_angry', brow_part_id:null, blush_opacity:0 });
  const renderer = rendererFor(value);
  renderer.expressionOverrideId = 'angry';

  assert.equal(renderer._partPose(angry, new Map(), 0).visible, true);
  assert.equal(renderer._partPose(neutral, new Map(), 0).visible, false);
  assert.equal(renderer._partPose(talk, new Map(), 0).visible, false);

  renderer.setSemanticState({ speaking:true, blink_allowed:true, blush:0, arousal:0 });
  assert.equal(renderer._partPose(talk, new Map(), 0).visible, true);
  assert.equal(renderer._partPose(angry, new Map(), 0).visible, false);
});
