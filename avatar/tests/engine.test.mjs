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
  };
  value.ik_chains = standardIK(value);
  return value;
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
  const fakeCanvas = { getContext: () => ({}) };
  const renderer = new AvatarRenderer(fakeCanvas);
  const value = project();
  renderer.setProject(value);
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
