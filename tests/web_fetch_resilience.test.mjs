import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../web/fetch-resilience.js', import.meta.url), 'utf8');
const preview = readFileSync(new URL('../web/realtime-preview.js', import.meta.url), 'utf8');
const app = readFileSync(new URL('../web/app-v2.js', import.meta.url), 'utf8');
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

test('surgical timing surfaces provider TTFT, context and remote DB cost', () => {
  assert.match(source, /X-Amora-Latency-Context-Ms/);
  assert.match(source, /X-Amora-Latency-Provider-Total-Ms/);
  assert.match(source, /X-Amora-Latency-Provider-First-Content-Ms/);
  assert.match(source, /X-Amora-Latency-Remote-Sql-Ms/);
  assert.match(source, /X-Amora-Latency-Remote-Sql-Calls/);
  assert.match(source, /`prov \$\{Math\.round\(trace\.provider\)\}`/);
  assert.match(source, /`ttft \$\{Math\.round\(trace\.firstContent\)\}`/);
  assert.match(source, /`ctx \$\{Math\.round\(trace\.context\)\}`/);
  assert.match(source, /`db \$\{Math\.round\(trace\.remoteSql\)\}\$\{calls\}`/);
});

test('progressive preview wraps fetch without stealing the app submit lifecycle', () => {
  assert.match(preview, /amoraProgressivePreviewFetch/);
  assert.match(preview, /X-Amora-Preview-Id/);
  assert.match(preview, /\/v1\/web\/state\?preview_id=/);
  assert.match(preview, /response\.clone\(\)\.json\(\)/);
  assert.match(preview, /reconcileFinal\(state, data\.reply_queued\)/);
  assert.doesNotMatch(preview, /addEventListener\("submit"/);
  assert.doesNotMatch(preview, /stopImmediatePropagation/);

  // app-v2 remains the sole owner of user-gesture playback unlock and of
  // claiming the canonical voice item after the POST completes.
  assert.match(app, /async function submitText\(text\)[\s\S]*?await unlockPlayback\(\)/);
  assert.match(app, /await api\("\/v1\/web\/utterance"[\s\S]*?await fillSpeechQueue\(\)/);
});

test('partial preview is ephemeral and only the validated final reply is persisted', () => {
  assert.match(preview, /renderPreview\(text\)/);
  assert.match(preview, /browserStore\.appendMessage\("amora", final\.text/);
  assert.doesNotMatch(preview, /appendMessage\("amora", text/);
});

test('resilience and preview shims load before the main Safari client', () => {
  const resilience = html.indexOf('./fetch-resilience.js');
  const realtime = html.indexOf('./realtime-preview.js');
  const appScript = html.indexOf('./app-v2.js');
  assert.ok(resilience >= 0);
  assert.ok(realtime > resilience);
  assert.ok(appScript > realtime);
});
