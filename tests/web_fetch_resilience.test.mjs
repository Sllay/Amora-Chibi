import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/fetch-resilience.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');

test('Safari voice stream is preflighted before the app sees it', () => {
  assert.match(source, /await reader\.read\(\)/);
  assert.match(source, /voiceStreamDowngraded = true/);
  assert.match(source, /replace\(\/\\\/stream\$\/, "\/audio"\)/);
  assert.match(source, /X-Amora-Transport-Fallback/);
  assert.match(source, /pcm_s16le/);
});

test('transport fallback never splices a second renderer after first PCM', () => {
  assert.match(source, /Once PCM reached Safari/);
  assert.match(source, /controller\.error\(error\)/);
});

test('server phase timing overrides the ambiguous total-only diagnostic', () => {
  assert.match(source, /timing\.cognition_ms/);
  assert.match(source, /timing\.language_social_ms/);
  assert.match(source, /timing\.local_overhead_ms/);
  assert.match(source, /compact\.join\(" · "\)/);
});

test('resilience shim loads before the main Safari client', () => {
  const resilience = html.indexOf('./fetch-resilience.js');
  const app = html.indexOf('./app-v2.js');
  assert.ok(resilience >= 0);
  assert.ok(app > resilience);
});
