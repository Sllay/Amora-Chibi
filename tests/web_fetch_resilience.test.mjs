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
  assert.match(source, /X-Amora-Latency-Trace-Version/);
  assert.match(source, /X-Amora-Latency-Context-Ms/);
  assert.match(source, /X-Amora-Latency-Provider-Total-Ms/);
  assert.match(source, /X-Amora-Latency-Provider-First-Content-Ms/);
  assert.match(source, /X-Amora-Latency-Remote-Sql-Ms/);
  assert.match(source, /X-Amora-Latency-Remote-Sql-Calls/);
  assert.match(source, /timing\.surgical/);
  assert.match(source, /`prov \$\{Math\.round\(trace\.provider\)\}`/);
  assert.match(source, /`ttft \$\{Math\.round\(trace\.firstContent\)\}`/);
  assert.match(source, /`ctx \$\{Math\.round\(trace\.context\)\}`/);
  assert.match(source, /`db \$\{Math\.round\(trace\.remoteSql\)\}\$\{calls\}`/);
  assert.match(source, /trace \$\{trace\.version \|\| "n\/a"\}/);
});

test('progressive preview wraps fetch without stealing the app submit lifecycle', () => {
  assert.match(preview, /amoraProgressivePreviewFetch/);
  assert.match(preview, /X-Amora-Preview-Id/);
  assert.match(preview, /\/v1\/web\/state\?preview_id=/);
  assert.match(preview, /response\.clone\(\)\.json\(\)/);
  assert.match(preview, /retirePreview\(state, data\.reply_queued\)/);
  assert.doesNotMatch(preview, /addEventListener\("submit"/);
  assert.doesNotMatch(preview, /stopImmediatePropagation/);

  // app-v2 remains the sole owner of user-gesture playback unlock and of
  // claiming the canonical voice item after the POST completes.
  assert.match(app, /async function submitText\(text\)[\s\S]*?await unlockPlayback\(\)/);
  assert.match(app, /await api\("\/v1\/web\/utterance"[\s\S]*?await fillSpeechQueue\(\)/);
});

test('partial preview is ephemeral and never persists the canonical reply', () => {
  assert.match(preview, /renderPreview\(text\)/);
  assert.match(preview, /state\.node\?\.remove\(\)/);
  assert.doesNotMatch(preview, /appendMessage\("amora"/);
  assert.match(preview, /app-v2 is the sole owner/);
});

test('health polling is coalesced and queue polling yields while a turn is active', () => {
  assert.match(source, /HEALTH_CACHE_MS = 4000/);
  assert.match(source, /HEALTH_STALE_GRACE_MS = 12000/);
  assert.match(source, /activeTurnRequests > 0/);
  assert.match(source, /EMPTY_QUEUE_CACHE_MS = 2000/);
  assert.match(source, /return jsonResponse\(\{ utterance: null \}\)/);
  assert.match(source, /url\.pathname === "\/v1\/audio\/transcribe"/);
});

test('resilience and preview shims are cache-busted before the main Safari client', () => {
  const resilience = html.indexOf('./fetch-resilience.js?v=20261030-74');
  const realtime = html.indexOf('./realtime-preview.js?v=20261030-74');
  const appScript = html.indexOf('./app-v2.js?v=20261030-74');
  assert.ok(resilience >= 0);
  assert.ok(realtime > resilience);
  assert.ok(appScript > realtime);
});
