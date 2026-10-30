import { BrowserState, browserStorage } from "./browser-state.mjs";

const browserStore = new BrowserState(browserStorage("localStorage"), browserStorage("sessionStorage"));
const PREVIEW_POLL_MS = 140;

const $ = (id) => document.getElementById(id);

function scrollMessages() {
  requestAnimationFrame(() => {
    const node = $("messages");
    if (node) node.scrollTop = node.scrollHeight;
  });
}

function renderMessage(role, text, extraClass = "") {
  const node = document.createElement("div");
  node.className = `message ${role}${extraClass ? ` ${extraClass}` : ""}`;
  node.textContent = text;
  $("messages")?.appendChild(node);
  scrollMessages();
  return node;
}

function persistAndRender(role, text, id = undefined, privacyScope = null) {
  if (typeof text !== "string" || !text.trim()) return null;
  if (browserStore.hasMessage(role, id)) return null;
  if (!browserStore.appendMessage(role, text, id, privacyScope)) return null;
  return renderMessage(role, text);
}

function setNotice(text) {
  const node = $("chatNotice");
  if (node) node.textContent = text;
}

function setMicStatus(text) {
  const visible = $("micState");
  const diag = $("diagMic");
  if (visible) visible.textContent = text;
  if (diag) diag.textContent = text;
}

function setTiming(timing, clientElapsed) {
  const node = $("diagTurn");
  if (!node) return;
  if (!timing || typeof timing.total_ms !== "number") {
    node.textContent = `${Math.round(clientElapsed)} ms`;
    return;
  }
  const values = [`${Math.round(timing.total_ms)} ms`];
  if (typeof timing.cognition_ms === "number") values.push(`cog ${Math.round(timing.cognition_ms)}`);
  if (typeof timing.language_social_ms === "number") values.push(`social ${Math.round(timing.language_social_ms)}`);
  if (typeof timing.local_overhead_ms === "number") values.push(`local ${Math.round(timing.local_overhead_ms)}`);
  node.textContent = values.join(" · ");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function authenticatedJson(path, options = {}) {
  const token = browserStore.pairing();
  if (!token) throw new Error("navegador ainda não pareado");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
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
      const data = await authenticatedJson(`/v1/web/previews/${encodeURIComponent(previewId)}`);
      const text = typeof data.text === "string" ? data.text.trim() : "";
      if (text && text !== last) {
        last = text;
        if (!state.node) state.node = renderMessage("amora", text, "preview");
        else state.node.textContent = text;
        scrollMessages();
      }
      if (data.done && state.requestDone) return;
    } catch (error) {
      if (error.status === 401) return;
    }
    await sleep(PREVIEW_POLL_MS);
  }
}

function finalDisplay(reply) {
  if (!reply) return { text: "", id: undefined, privacyScope: null };
  const metadata = reply.metadata || {};
  return {
    text: String(reply.display_text ?? metadata.display_text ?? reply.text ?? "").trim(),
    id: metadata.cognitive_turn_id || reply.id,
    privacyScope: reply.privacy_scope || null,
  };
}

async function submitRealtimeText(event) {
  event.preventDefault();
  event.stopImmediatePropagation();

  const input = $("textInput");
  const clean = String(input?.value || "").trim();
  if (!clean) return;
  if (input) {
    input.value = "";
    input.style.height = "auto";
  }

  const stop = $("stopVoice");
  if (stop && !stop.disabled) stop.click();

  persistAndRender("user", clean);
  setNotice("");
  setMicStatus("Amora pensando…");
  const started = performance.now();
  const previewId = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
    || `${Date.now()}${Math.random().toString(36).slice(2)}`;
  const previewState = { node: null, finished: false, requestDone: false };
  const poller = pollPreview(previewId, previewState);

  try {
    const data = await authenticatedJson("/v1/web/utterance", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Amora-Preview-Id": previewId,
      },
      body: JSON.stringify({
        text: clean,
        remote_processing_allowed: Boolean($("remoteToggle")?.checked),
        test_mode: Boolean($("testToggle")?.checked),
      }),
    });
    previewState.requestDone = true;
    const final = finalDisplay(data.reply_queued);
    if (final.text) {
      if (browserStore.hasMessage("amora", final.id)) {
        previewState.node?.remove();
      } else if (browserStore.appendMessage("amora", final.text, final.id, final.privacyScope)) {
        if (previewState.node) {
          previewState.node.textContent = final.text;
          previewState.node.classList.remove("preview");
        } else {
          renderMessage("amora", final.text);
        }
      }
    } else {
      previewState.node?.remove();
    }
    setTiming(data.timing, performance.now() - started);
    scrollMessages();
  } catch (error) {
    previewState.requestDone = true;
    previewState.node?.remove();
    setNotice(`Erro no turno: ${error.message}`);
  } finally {
    previewState.finished = true;
    await poller;
    setMicStatus("microfone desligado");
  }
}

const form = $("textForm");
if (form) {
  // Registered before app-v2.js. stopImmediatePropagation keeps the old
  // request/blank-screen path from also submitting the same turn.
  form.addEventListener("submit", submitRealtimeText, { capture: true });
}
