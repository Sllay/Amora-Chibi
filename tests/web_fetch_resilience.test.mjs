import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/fetch-resilience.js', import.meta.url), 'utf8');
const preview = readFileSync(new URL('../web/realtime-preview.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../web/index.html', import.meta.url), 'utf8');

test('Safari voice stream is preflighted before the app sees it', () => {
  assert.match(source, /await reader\.read\(\)/);
  assert.match(source, /voiceStreamDowngraded = true/);
  assert.match(source, /pathname\.replace\(\/\\\/stream\$\/, "\/audio"\)/);
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

test('progressive preview sends an ephemeral id and never persists partial text', () => {
  assert.match(preview, /X-Amora-Preview-Id/);
  assert.match(preview, /\/v1\/web\/state\?preview_id=/);
  assert.match(preview, /previewState\.node\.textContent = final\.text/);
  assert.doesNotMatch(preview, /appendMessage\("amora", text/);
});

test('resilience and preview shims load before the main Safari client', () => {
  const resilience = html.indexOf('./fetch-resilience.js');
  const realtime = html.indexOf('./realtime-preview.js');
  const app = html.indexOf('./app-v2.js');
  assert.ok(resilience >= 0);
  assert.ok(realtime > resilience);
  assert.ok(app > realtime);
});
