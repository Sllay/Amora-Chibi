// Executes the shipped browser app. Network and physical Web Audio are the only
// seams; pairing/history/queue/cancel/player wiring are the production modules.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { BrowserState } from '../../web/browser-state.mjs';
import { WavPlayer } from '../../web/audio-player.mjs';

export function storage() {
  const data = new Map();
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key), data };
}
export const token = `browser.v2.${Buffer.from(`web:${'a'.repeat(32)}`).toString('base64url')}.${'b'.repeat(64)}`;
const source = readFileSync(new URL('../../web/app-v2.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../../web/index.html', import.meta.url), 'utf8');

class Element {
  constructor() {
    this.children = []; this.textContent = ''; this.value = ''; this.style = {}; this.listeners = {}; this.attributes = {}; this.open = false;
    const classes = new Set();
    this.classList = { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name), toggle: (name, flag) => flag ? classes.add(name) : classes.delete(name) };
  }
  appendChild(node) { this.children.push(node); node.parent = this; }
  replaceChildren() { this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(key, value) { this.attributes[key] = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

export async function app(local, options = {}) {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const calls = [], sources = [], contexts = [], intervals = [], windowListeners = {};
  const fetch = async (path, request = {}) => {
    calls.push({ path, request });
    if (options.fetch) {
      const response = await options.fetch(path, request);
      if (response !== undefined) return response;
    }
    if (path === '/health') return Response.json({ status: 'ok' });
    if (path === '/v1/pairing/redeem') return Response.json({ token, token_type: 'Bearer' });
    if (path === '/v1/web/state') return Response.json({ voice_transport: 'amora_remote_supertonic_world', local_voice: {} }, { status: options.authStatus || 200 });
    if (path.startsWith('/v1/conversation/next')) return Response.json({ utterance: null });
    if (path.endsWith('/audio')) return Response.json({ detail: 'VOICE_WORKER_UNAVAILABLE' }, { status: 503 });
    if (/\/(cancel|started|ack)$/.test(path)) return Response.json({});
    if (path === '/v1/web/utterance') return Response.json({});
    throw new Error(`Unexpected request ${path}`);
  };
  const window = { addEventListener(name, fn) { windowListeners[name] = fn; }, AudioContext: class {
    constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; contexts.push(this); }
    async resume() { this.state = 'running'; }
    async decodeAudioData(bytes) { return options.decode ? options.decode(bytes) : { duration: 0.1 }; }
    createBufferSource() {
      const source = { connect() {}, disconnect() {}, start() { this.started = true; }, stop() { this.stopped = true; }, end() { this.onended?.(); } };
      sources.push(source); return source;
    }
  } };
  const context = vm.createContext({ BrowserState, WavPlayer, browserStorage: name => name === 'localStorage' ? local : storage(), window,
    document: { getElementById: id => nodes.get(id), createElement: () => new Element(), addEventListener() {} },
    fetch, Headers, URL, URLSearchParams, AbortController, DOMException, performance, setTimeout, clearTimeout,
    setInterval: fn => intervals.push(fn), requestAnimationFrame: fn => fn(),
  });
  vm.runInContext(source.replace(/^import .*;\n/gm, '').replaceAll('import.meta.url', '"https://amora.example/web/app-v2.js"'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { context, nodes, calls, sources, contexts, intervals, windowListeners, state: vm.runInContext('state', context),
    messages: () => nodes.get('messages').children.map(node => ({ role: node.className.split(' ')[1], text: node.textContent })) };
}
