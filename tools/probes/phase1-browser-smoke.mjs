// Real Chromium + shipped Web UI + isolated Core. No provider or TTS requests.
// Run with AMORA_PYTHON=/path/to/python node tools/probes/phase1-browser-smoke.mjs
// Requires playwright and Chromium; does not install them or touch real runtime.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const runtime = await mkdtemp(join(tmpdir(), 'amora-phase1-'));
const port = Number(process.env.AMORA_AUDIT_PORT || 18765);
const origin = `http://127.0.0.1:${port}`;
const tokenPath = join(runtime, 'browser-token');
const code = `
import os
from pathlib import Path
import uvicorn
from amora.app import AmoraCore
from amora.api import create_api
from amora.security import issue_browser_token
core=AmoraCore.bootstrap()
p=Path(os.environ['AMORA_AUDIT_TOKEN_FILE'])
p.write_text(issue_browser_token(core.secrets.get('LOCAL_API_TOKEN'),core.clock,'web'))
p.chmod(0o600)
uvicorn.run(create_api(core),host='127.0.0.1',port=int(os.environ['AMORA_AUDIT_PORT']),log_level='error')
`;
const server = spawn(process.env.AMORA_PYTHON || 'python', ['-c', code], {
  env: { ...process.env, AMORA_HOME: runtime, AMORA_MODE: 'local', AMORA_WEB_VOICE_BACKEND: 'local',
    AMORA_AUDIT_PORT: String(port), AMORA_AUDIT_TOKEN_FILE: tokenPath }, stdio: ['ignore', 'ignore', 'pipe'],
});
// No subprocess output or credentials are printed.
server.stderr.resume();
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { const r = await fetch(`${origin}/health`); ready = r.ok; } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error('Isolated Core exited before readiness');
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, 'Core readiness timeout');
  const token = await readFile(tokenPath, 'utf8');
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const errors = [], submitted = [], responses = [];
  page.on("response", res => { if(res.url().startsWith(origin)) responses.push({path:new URL(res.url()).pathname,status:res.status()}); });
  page.on('pageerror', error => errors.push(error.name));
  page.on('request', req => { if (req.url().endsWith('/v1/web/utterance/stream')) submitted.push(req.method()); });
  await page.addInitScript(({token}) => {
    localStorage.setItem('amora.web.pairing.v2', token);
    localStorage.setItem('amora.web.voice-muted.v1', '1');
    const fetchOriginal = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await fetchOriginal(...args);
      if (String(args[0]).endsWith('/v1/web/utterance/stream')) {
        response.clone().text().then(body => { window.__auditSSE = body; });
      }
      return response;
    };
  }, {token});
  await page.goto(`${origin}/web/`);
  await page.locator('#appPanel:not(.hidden)').waitFor();
  await page.locator('#remoteToggle').uncheck();
  await page.locator('#textInput').fill('Amora');
  await page.locator('#textForm button[type=submit]').click();
  await page.waitForFunction(() => Boolean(window.__auditSSE), null, {timeout: 8000});
  const sse = await page.evaluate(() => window.__auditSSE);
  const block = sse.split('\n\n').find(value => value.startsWith('event: completed'));
  assert.ok(block, 'SSE completed missing');
  const final = JSON.parse(block.split('\n').find(line => line.startsWith('data:')).slice(5));
  const expected = final.reply_queued?.display_text || final.reply_queued?.text;
  assert.ok(expected, 'No queued reply from Core');
  try { await page.waitForFunction(text => document.querySelector('#messages').textContent.includes(text), expected, {timeout: 8000}); }
  catch (error) {
    console.log(JSON.stringify({diagnostic: await page.locator('#messages').innerText(), notice: await page.locator('#chatNotice').innerText(), submitted, responses, errors}));
    throw error;
  }
  const auth = await page.evaluate(async (token) => {
    const unauthorized = await fetch('/v1/web/state');
    const operator = await fetch('/v1/operator/commands/catalog', { headers: { Authorization: `Bearer ${token}` }});
    return [unauthorized.status, operator.status];
  }, token);
  assert.deepEqual(auth, [401, 403]);
  assert.deepEqual(submitted, ['POST']);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ browser: 'Chromium', shipped_ui: true, authenticated_sse: true,
    visible_reply: expected, posts: submitted.length, unauthenticated_http: auth[0],
    web_operator_http: auth[1], page_errors: errors.length, remote_generation: false, physical_audio: false }));
} finally {
  await browser?.close();
  if (server.exitCode === null) { const done = once(server, 'exit'); server.kill('SIGTERM'); await done; }
  await rm(runtime, { recursive: true, force: true });
}
