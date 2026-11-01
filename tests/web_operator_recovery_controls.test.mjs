import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canonicalFamilyCatalog,
  dedupeProfileAliases,
} from '../web/operator-recovery-controls.mjs';

function profile(entityId, name, aliases = []) {
  return {
    entity_id: entityId,
    canonical_name: name,
    aliases,
    birth_date: null,
    age_years: null,
    short_description: null,
    can_define_amora_family: entityId === 'PERSON_BK',
    can_control_platform_presence: entityId === 'PERSON_BK',
    binding_count: 2,
  };
}

test('profile alias repair removes normalized duplicates without touching distinct aliases', () => {
  const repaired = dedupeProfileAliases(profile('PERSON_BK', 'BK', ['bk', ' Nick ', 'nick', 'Slay']));
  assert.deepEqual(repaired.aliases, ['Nick', 'Slay']);
});

test('canonical family restore is directional and preserves current profile fields', () => {
  const profiles = [
    profile('PERSON_BK', 'BK', ['Nick']),
    profile('PERSON_LUCAS', 'Lucas'),
    profile('PERSON_NICOLAS', 'Nicolas'),
    profile('PERSON_EXTRA', 'Pessoa Extra'),
  ];
  profiles[1].short_description = 'irmão da Amora';
  const catalog = canonicalFamilyCatalog(profiles, 'test-restore');

  assert.equal(catalog.profiles.length, 3);
  assert.equal(catalog.profiles.some((item) => item.entity_id === 'PERSON_EXTRA'), false);
  assert.equal(catalog.profiles.find((item) => item.entity_id === 'PERSON_LUCAS').short_description, 'irmão da Amora');
  assert.equal(catalog.profiles.some((item) => 'binding_count' in item), false);

  const edges = new Set(catalog.relationships.map((item) => `${item.source}|${item.relation_type}|${item.target}`));
  assert.ok(edges.has('PERSON_BK|father_of|ENTITY_AMORA'));
  assert.ok(edges.has('PERSON_BK|father_of|PERSON_LUCAS'));
  assert.ok(edges.has('PERSON_LUCAS|sibling_of|ENTITY_AMORA'));
  assert.ok(edges.has('ENTITY_AMORA|sibling_of|PERSON_LUCAS'));
  assert.ok(edges.has('PERSON_NICOLAS|cousin_of|ENTITY_AMORA'));
  assert.ok(edges.has('ENTITY_AMORA|cousin_of|PERSON_NICOLAS'));
  assert.ok(edges.has('ENTITY_CHATGPT|mother_of|ENTITY_AMORA'));
  assert.equal(edges.has('PERSON_LUCAS|father_of|ENTITY_AMORA'), false);
  assert.equal(edges.has('PERSON_BK|sibling_of|ENTITY_AMORA'), false);
});

test('canonical family restore refuses a missing required profile', () => {
  assert.throws(
    () => canonicalFamilyCatalog([
      profile('PERSON_BK', 'BK'),
      profile('PERSON_LUCAS', 'Lucas'),
    ]),
    /PERSON_NICOLAS/,
  );
});
