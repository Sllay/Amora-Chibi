import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const sw = readFileSync(new URL('../sw.js', import.meta.url), 'utf8');
const bundleUi = readFileSync(new URL('../bundle-ui.js', import.meta.url), 'utf8');

test('portable bundle UI is loaded through a CSP-safe same-origin module', () => {
  assert.match(html, /<script type="module" src="\.\/bundle-ui\.js"><\/script>/);
  assert.doesNotMatch(html, /<script type="module">/);
  assert.match(bundleUi, /fetch\('\/v1\/avatar\/bundle'/);
  assert.match(bundleUi, /amora_avatar\.zip/);
  assert.match(bundleUi, /application\/zip/);
});

test('portable bundle UI participates in the Avatar offline shell', () => {
  assert.match(sw, /['"]\/avatar\/bundle-ui\.js['"]/);
  assert.match(sw, /amora-avatar-v09-bundle-20261030/);
});
