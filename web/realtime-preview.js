import { BrowserState, browserStorage } from "./browser-state.mjs";

const browserStore = new BrowserState(browserStorage("localStorage"), browserStorage("sessionStorage"));
const PREVIEW_POLL_MS = 200;
const upstreamFetch = window.fetch.bind(window);

const $ = (id) => document.getElementById(id);

function asUrl(input) {
  const raw = typeof input === "string" || input instanceof URL ? input : input?.url;
  return new URL(raw, window.location.href);
}

function isWebTurn(url, init) {
  return url.pathname === "/v1/web/utterance" && String(init?.method || "GET").toUpperCase() === "POST";
}

function scrollMessages() {
  requestAnimationFrame(() => {
    const node = $("messages");
    if (node) node.scrollTop = node.scrollHeight;
  });
}

function pruneLiveMessages() {
  const container = $("messages");
  if (!container) return;
  const counts = { user: 0, amora: 0 };
  for (const child of [...container.children].reverse()) {
    const role = child.classList.contains("user") ? "user" : child.classList.contains("amora") ? "amora" : null;
    if (!role) continue;
    counts[role] += 1;
    if (counts[role] > 50) child.remove();
  }
}

function renderPreview(text) {
  const node = document.createElement("div");
  node.className = "message amora preview";
  node.textContent = text;
  $("messages")?.appendChild(node);
  pruneLiveMessages();
  scrollMessages();
  return node;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function authenticatedJson(path) {
  const token = browserStore.pairing();
  if (!token) throw new Error("navegador ainda não pareado");
  const headers = new Headers({ Authorization: `Bearer ${token}` });
  const response = await upstreamFetch(path, { headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try { detail = JSON.parse(body)?.detail || body; } catch (_) {}
    const error = new Error(`${response.status}: ${detail || response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function pollPreview(previewId, state) {
  let last = "";
  while (!state.finished) {
    try {
      const data = await authenticatedJson(`/v1/web/state?preview_id=${encodeURIComponent(previewId)}`);
      const text = typeof data.text === "string" ? data.text.trim() : "";
      if (text && text !== last) {
        last = text;
        if (!state.node) state.node = renderPreview(text);
        else state.node.textContent = text;
        scrollMessages();
      }
      if (data.done && state.requestDone) return;
    } catch (error) {
      if (error.status === 401 || error.status === 403) return;
    }
    await sleep(PREVIEW_POLL_MS);
  }
}

function finalDisplay(reply) {
  if (!reply) return "";
  const metadata = reply.metadata || {};
  return String(reply.display_text ?? metadata.display_text ?? reply.text ?? "").trim();
}

function retirePreview(state, reply) {
  // Preview is deliberately reversible and ephemeral. app-v2 is the sole owner
  // of canonical chat persistence/rendering once the queued utterance is claimed.
  // Keeping those responsibilities separate prevents one turn from appearing
  // twice when the preview finishes immediately before fillSpeechQueue().
  const finalText = finalDisplay(reply);
  if (state.node && finalText) state.node.textContent = finalText;
  state.node?.remove();
  state.node = null;
}

function newPreviewId() {
  const randomId = globalThis.crypto?.randomUUID?.() || "";
  return randomId.replace(/-/g, "") || `${Date.now()}${Math.random().toString(36).slice(2)}`;
}

window.fetch = async function amoraProgressivePreviewFetch(input, init = {}) {
  const url = asUrl(input);
  if (!isWebTurn(url, init)) return upstreamFetch(input, init);

  const headers = new Headers(init.headers || {});
  if (headers.has("X-Amora-Preview-Id")) return upstreamFetch(input, init);

  const previewId = newPreviewId();
  headers.set("X-Amora-Preview-Id", previewId);
  const state = { node: null, finished: false, requestDone: false };
  const poller = pollPreview(previewId, state);

  try {
    const response = await upstreamFetch(input, { ...init, headers });
    state.requestDone = true;
    if (response.ok) {
      try {
        const data = await response.clone().json();
        retirePreview(state, data.reply_queued);
      } catch (_) {
        state.node?.remove();
      }
    } else {
      state.node?.remove();
    }
    return response;
  } catch (error) {
    state.requestDone = true;
    state.node?.remove();
    throw error;
  } finally {
    state.finished = true;
    void poller.catch(() => {});
  }
};
