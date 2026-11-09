// Real Chromium + shipped Web UI + isolated Core. No provider or TTS requests.
// Run with AMORA_PYTHON=/path/to/python node tools/probes/phase3-browser-perspective.mjs
// Requires playwright and Chromium; does not install them or touch real runtime.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import assert from 'node:assert/strict';
const { chromium } = createRequire(import.meta.url)('playwright');
const runtime = await mkdtemp(join(tmpdir(), 'amora-phase3-'));
const port = Number(process.env.AMORA_AUDIT_PORT || 18766);
const origin = `http://127.0.0.1:${port}`;
const tokenPath = join(runtime, 'browser-token');
const code = `
import os, json
from pathlib import Path
import uvicorn
from amora.app import AmoraCore
from amora.api import create_api
from amora.security import issue_browser_token
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider
async def fake_post(self, payload, *, api_key):
    system=payload['messages'][0]['content']
    frame,_=json.JSONDecoder().raw_decode(system.split('AMORA TURN FRAME — TRUSTED CORE DATA\\n',1)[1])
    roles=frame['current_turn']['acir']['proposition']['roles']
    refs=roles['references']
    assert roles['assertion_truth']=='unverified_user_claim'
    current=frame['current_turn']
    text=payload['messages'][-1]['content']
    index=fake_post.calls
    expected=['ENTITY_CHATGPT','ENTITY_CHATGPT',None,'ENTITY_CHATGPT'][index]
    if expected: assert any(r['entity_ids']==[expected] for r in refs)
    else: assert not any(r['entity_ids']==['ENTITY_CHATGPT'] for r in refs)
    fake_post.calls+=1
    response='Perspectiva validada '+str(fake_post.calls)+'.'
    return {'choices':[{'message':{'content':json.dumps({'moves':[{'kind':frame['dialogue_plan']['response_moves'][0],'text':response}], 'closing':'statement'})}}]}, {}, 200
fake_post.calls=0
NvidiaMistralLarge4ProductionProvider._post_chat=fake_post
core=AmoraCore.bootstrap()
p=Path(os.environ['AMORA_AUDIT_TOKEN_FILE'])
p.write_text(issue_browser_token(core.secrets.get('LOCAL_API_TOKEN'),core.clock,'web'))
p.chmod(0o600)
uvicorn.run(create_api(core),host='127.0.0.1',port=int(os.environ['AMORA_AUDIT_PORT']),log_level='error')
`;
const server = spawn(process.env.AMORA_PYTHON || 'python', ['-c', code], {
  env: { ...process.env, AMORA_HOME: runtime, AMORA_MODE: 'local', AMORA_SECRET_NVIDIA_API_KEY: 'synthetic-never-remote', AMORA_SOCIAL_EXPRESSION_PROFILE: 'core-v1', AMORA_WEB_VOICE_BACKEND: 'local',
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
  page.on('request', req => { if (req.url().endsWith('/v1/web/utterance/stream')) submitted.push(JSON.parse(req.postData())); });
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
  await page.locator('#remoteToggle').check();
  await page.locator('#testToggle').check();
  const visible = [];
  for (const [index, text] of ['Sua mãe me ama', 'A sua mae mim amar', 'Minha mãe me ama', 'Sua mãe contou uma piada. Ela me ama'].entries()) {
    await page.evaluate(() => { window.__auditSSE = null; });
    await page.locator('#textInput').fill(text);
    await page.locator('#textForm button[type=submit]').click();
    await page.waitForFunction(() => Boolean(window.__auditSSE), null, {timeout: 12000});
    const sse = await page.evaluate(() => window.__auditSSE);
    const block = sse.split('\n\n').find(value => value.startsWith('event: completed'));
    assert.ok(block, 'SSE completed missing');
    const final = JSON.parse(block.split('\n').find(line => line.startsWith('data:')).slice(5));
    const expected = `Perspectiva validada ${index + 1}.`;
    assert.equal(final.reply_queued?.display_text || final.reply_queued?.text, expected);
    await page.waitForFunction(text => document.querySelector('#messages').textContent.includes(text), expected, {timeout: 8000});
    visible.push(expected);
  }
  assert.equal(submitted.length, 4);
  assert.ok(submitted[0].session_id);
  assert.equal(new Set(submitted.map(item => item.session_id)).size, 1);
  const auth = await page.evaluate(async (token) => {
    const unauthorized = await fetch('/v1/web/state');
    const operator = await fetch('/v1/operator/commands/catalog', { headers: { Authorization: `Bearer ${token}` }});
    return [unauthorized.status, operator.status];
  }, token);
  assert.deepEqual(auth, [401, 403]);
  assert.ok(submitted.every(item => item.test_mode && item.remote_processing_allowed));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ browser: 'Chromium', shipped_ui: true, authenticated_sse: true,
    visible_replies: visible, stable_tab_session: true, perspective_payload_assertions: true, posts: submitted.length, unauthenticated_http: auth[0],
    web_operator_http: auth[1], page_errors: errors.length, remote_generation: false, fake_nvidia_transport: true, physical_audio: false }));
} finally {
  await browser?.close();
  if (server.exitCode === null) { const done = once(server, 'exit'); server.kill('SIGTERM'); await done; }
  await rm(runtime, { recursive: true, force: true });
}
