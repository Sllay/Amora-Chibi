import { BrowserState, browserStorage } from "./browser-state.mjs";
import { PcmStreamPlayer, WavPlayer } from "./audio-player.mjs";

const $ = (id) => document.getElementById(id);
const browserStore = new BrowserState(browserStorage("localStorage"), browserStorage("sessionStorage"));
const REMOTE_KEY = "amora.web.remote.v1";
const TEST_KEY = "amora.web.test.v1";
const MAX_SPEECH_QUEUE = 3;
const VOICE_FETCH_TIMEOUT_MS = 90000;
const STREAM_BACKPRESSURE_HIGH_SECONDS = 1.25;
const STREAM_BACKPRESSURE_LOW_SECONDS = 0.85;

const state = {
  token: browserStore.pairing(),
  fillBusy: false,
  queueError: "",
  speechQueue: [],
  activeRecord: null,
  activeSource: null,
  preparingRecord: null,
  speechEpoch: 0,
  voiceMuted: browserStore.voiceMuted(),
  voiceStreaming: false,
  claimed: new Map(),
  suppressedTurns: new Set(),
  pendingCancels: new Map(),
  cancelling: new Set(),
  displayedTurns: new Set(browserStore.messages.filter(item => item.role === "amora").map(item => item.id).filter(Boolean)),
  lastSpeechEndAt: 0,
  lastTurnStartedAt: 0,
  playbackContext: null,
  playbackUnlocked: false,
  voiceErrorShown: false,
  micEnabled: false,
  mediaStream: null,
  audioContext: null,
  captureNode: null,
  muteNode: null,
  noiseFloor: 0.006,
  preRoll: [],
  preRollMs: 0,
  aboveMs: 0,
  userSpeaking: false,
  speechParts: [],
  speechMs: 0,
  silenceMs: 0,
  sendingAudio: false,
};
const voicePlayer = new WavPlayer(getPlaybackContext);

const pairPanel = $("pairPanel");
const appPanel = $("appPanel");
const remoteToggle = $("remoteToggle");
const testToggle = $("testToggle");
remoteToggle.checked = browserStore.get(REMOTE_KEY) !== "0";
testToggle.checked = browserStore.get(TEST_KEY) !== "0";
remoteToggle.addEventListener("change", () => browserStore.set(REMOTE_KEY, remoteToggle.checked ? "1" : "0"));
testToggle.addEventListener("change", () => browserStore.set(TEST_KEY, testToggle.checked ? "1" : "0"));

function setCoreStatus(text, mode = "") {
  $("coreStatus").textContent = text;
  $("coreDot").className = `dot ${mode}`.trim();
  $("diagCore").textContent = text;
}

function setMicStatus(text) {
  $("micState").textContent = text;
  $("diagMic").textContent = text;
}

function setVoiceStatus(text) {
  const node = $("diagVoice");
  if (node) node.textContent = text;
}

function updateQueueDiag() {
  $("diagQueue").textContent = state.queueError || String(state.speechQueue.length);
  $("stopVoice").disabled = state.speechQueue.length === 0 && !state.activeRecord;
}

function renderMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text;
  $("messages").appendChild(node);
  // Apply the same turn limits to the live DOM as to persistent history.
  const counts = { user: 0, amora: 0 };
  for (const child of [...$("messages").children].reverse()) {
    const role = child.className.split(" ")[1];
    counts[role] = (counts[role] || 0) + 1;
    if (counts[role] > 50) child.remove();
  }
  requestAnimationFrame(() => {
    $("messages").scrollTop = $("messages").scrollHeight;
  });
}

function addMessage(role, text, id = undefined, privacyScope = null) {
  if (typeof text !== "string" || !text.trim()) return;
  if (role === "system") {
    $("chatNotice").textContent = text;
    return;
  }
  if (!["user", "amora"].includes(role)) return;
  if (browserStore.hasMessage(role, id)) return;
  browserStore.appendMessage(role, text, id, privacyScope);
  renderMessage(role, text);
}

let historyRestored = false;
function restoreConversation() {
  if (historyRestored) return;
  historyRestored = true;
  for (const item of browserStore.messages) renderMessage(item.role, item.text);
}

$("clearConversation").addEventListener("click", () => $("clearDialog").showModal());
$("cancelClear").addEventListener("click", () => $("clearDialog").close());
$("confirmClear").addEventListener("click", () => {
  browserStore.clearHistory();
  $("messages").replaceChildren();
  $("clearDialog").close();
});
restoreConversation();

function updateMuteControl() {
  $("muteVoice").textContent = `Voz da Amora: ${state.voiceMuted ? "mutada" : "ligada"}`;
  $("muteVoice").setAttribute("aria-pressed", String(state.voiceMuted));
  if (state.voiceMuted) setVoiceStatus("mutada · texto ativo");
}
$("muteVoice").addEventListener("click", async () => {
  state.voiceMuted = !state.voiceMuted;
  browserStore.setVoiceMuted(state.voiceMuted);
  updateMuteControl();
  if (state.voiceMuted) await interruptVoice("web_muted", null, true);
  else await unlockPlayback();
  await fillSpeechQueue();
});
updateMuteControl();

async function api(path, options = {}, authenticated = true) {
  const headers = new Headers(options.headers || {});
  if (authenticated) {
    if (!state.token) throw new Error("navegador ainda não pareado");
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try { detail = JSON.parse(body)?.detail || body; } catch (_) {}
    const error = new Error(`${response.status}: ${detail || response.statusText}`);
    error.status = response.status;
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

async function apiBinary(path, options = {}) {
  if (!state.token) throw new Error("navegador ainda não pareado");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.token}`);
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try { detail = JSON.parse(body)?.detail || body; } catch (_) {}
    const error = new Error(`${response.status}: ${detail || response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return response;
}

async function health() {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const data = await response.json();
    setCoreStatus(data.status === "ok" ? `online · ${data.version || "Core"}` : data.status, data.status === "ok" ? "ok" : "bad");
    return data.status === "ok";
  } catch (_) {
    setCoreStatus("offline", "bad");
    return false;
  }
}

async function validatePairing() {
  if (!state.token) return false;
  try {
    const data = await api("/v1/web/state");
    const voice = data.voice || data.local_voice || {};
    const transport = String(data.voice_transport || "");
    const active = voice.last_provider || (voice.fish_configured ? "fish" : voice.gemini_configured ? "gemini" : "");
    const fetchStreaming = typeof ReadableStream !== "undefined" && typeof Response !== "undefined" && "body" in Response.prototype;
    state.voiceStreaming = Boolean(voice.streaming && fetchStreaming);
    let label = "Amora · voz do Core";
    if (transport === "fish_s2_1_pro_free_then_gemini") label = "Amora · Fish S2.1 Pro";
    else if (transport === "amora_remote_supertonic_world") label = "Amora · worker remoto Supertonic";
    else if (transport === "amora_local_supertonic_world") label = "Amora · backend local Supertonic";
    $("voiceLabel").textContent = label;
    const post = voice.audio_postprocessing ? ` · DSP ${voice.audio_postprocessing}` : "";
    const stream = state.voiceStreaming ? " · stream PCM" : "";
    setVoiceStatus(voice.available
      ? (active ? `disponível · ${active}${stream}${post}` : `disponível${stream}${post}`)
      : (voice.last_error || "voz indisponível · verifique secrets/configuração"));
    if (state.voiceMuted) setVoiceStatus("mutada · texto ativo");
    return true;
  } catch (error) {
    if (error.status === 401) {
      browserStore.forgetPairing();
      state.token = "";
    }
    return false;
  }
}

function getPlaybackContext() {
  if (state.playbackContext) return state.playbackContext;
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) throw new Error("Web Audio não disponível neste navegador");
  state.playbackContext = new AudioContextCtor({ latencyHint: "interactive" });
  return state.playbackContext;
}

async function unlockPlayback() {
  if (state.voiceMuted) return false;
  const context = getPlaybackContext();
  if (context.state === "suspended") {
    try { await context.resume(); } catch (_) {}
  }
  state.playbackUnlocked = context.state === "running";
  if (state.playbackUnlocked) {
    setVoiceStatus("player liberado");
    pumpPlayback();
  }
  return state.playbackUnlocked;
}

async function showCorrectSurface() {
  const ok = await health();
  const paired = ok && await validatePairing();
  // A transient outage is not revocation: keep the saved pairing and chat.
  const hasCredential = Boolean(state.token);
  pairPanel.classList.toggle("hidden", hasCredential);
  appPanel.classList.toggle("hidden", !hasCredential);
  if (paired) {
    addMessage("system", "Safari Lab conectado. Texto pronto para conversar; modo teste fica ligado por padrão.");
    fillSpeechQueue();
  }
}

$("pairForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("pairError").textContent = "";
  const code = $("pairCode").value.replace(/\D/g, "").slice(0, 6);
  if (code.length !== 6) {
    $("pairError").textContent = "O código precisa ter 6 dígitos.";
    return;
  }
  try {
    const data = await api("/v1/pairing/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }, false);
    state.token = browserStore.savePairing(data.token);
    $("pairCode").value = "";
    await unlockPlayback();
    await showCorrectSurface();
  } catch (error) {
    $("pairError").textContent = `Pareamento falhou: ${error.message}`;
  }
});

$("forgetPairing").addEventListener("click", async () => {
  browserStore.forgetPairing();
  const stopping = interruptVoice("web_unpair", null, true);
  state.token = "";
  pairPanel.classList.remove("hidden");
  appPanel.classList.add("hidden");
  await disableMicrophone();
  await stopping;
});

async function submitText(text) {
  const clean = text.trim();
  if (!clean) return;
  await unlockPlayback();
  if (state.activeRecord || state.speechQueue.length) {
    await interruptVoice("web_text_barge_in", null, false);
  }
  state.lastTurnStartedAt = performance.now();
  addMessage("user", clean);
  setMicStatus("Amora pensando…");
  try {
    await api("/v1/web/utterance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: clean,
        remote_processing_allowed: remoteToggle.checked,
        test_mode: testToggle.checked,
      }),
    });
    $("diagTurn").textContent = `${Math.round(performance.now() - state.lastTurnStartedAt)} ms`;
    await fillSpeechQueue();
  } catch (error) {
    addMessage("system", `Erro no turno: ${error.message}`);
  } finally {
    setMicStatus(state.micEnabled ? "ouvindo" : "microfone desligado");
  }
}

$("textForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("textInput");
  const text = input.value;
  input.value = "";
  input.style.height = "auto";
  await submitText(text);
});

$("textInput").addEventListener("input", (event) => {
  const node = event.currentTarget;
  node.style.height = "auto";
  node.style.height = `${Math.min(node.scrollHeight, 120)}px`;
});

function cognitiveTurnId(item) {
  return item?.metadata?.cognitive_turn_id || null;
}

async function claimNext(turnId = null) {
  const params = new URLSearchParams({ device_id: "WEB_SAFARI" });
  if (turnId) params.set("cognitive_turn_id", turnId);
  const data = await api(`/v1/conversation/next?${params}`);
  return data.utterance || null;
}

function queueLocalSpeech(item, claimEpoch = state.speechEpoch) {
  // A response's literal display text belongs to its turn, never to its TTS ID.
  const displayId = cognitiveTurnId(item) || item.id;
  if (!state.displayedTurns.has(displayId)) {
    addMessage("amora", item.display_text ?? item.text, displayId, item.privacy_scope);
    state.displayedTurns.add(displayId);
    while (state.displayedTurns.size > 512) state.displayedTurns.delete(state.displayedTurns.values().next().value);
  }
  if (state.claimed.has(item.id)) return;
  state.claimed.set(item.id, item);
  if (state.voiceMuted || claimEpoch !== state.speechEpoch
      || state.suppressedTurns.has(cognitiveTurnId(item)) || !item.text.trim()) {
    cancelVoiceItem(item, state.voiceMuted ? "web_muted" : "web_voice_suppressed");
    return;
  }
  const record = {
    item,
    epoch: state.speechEpoch,
    status: "queued",
    audioBuffer: null,
    streamPlayer: null,
    streamStarted: false,
    voiceProvider: "",
    voiceModel: "",
    voiceFallback: "",
    audioPostprocessing: "",
    controller: new AbortController(),
    interrupted: false,
  };
  state.speechQueue.push(record);
  updateQueueDiag();
  pumpPreparation();
}

function pumpPreparation() {
  if (state.voiceMuted || state.preparingRecord) return;
  if (state.voiceStreaming && state.activeRecord) return;
  const record = state.speechQueue.find(item => item.status === "queued" && item.epoch === state.speechEpoch);
  if (!record) return;
  state.preparingRecord = record;
  record.status = "preparing";
  prepareLocalSpeech(record);
}

async function prepareLocalSpeech(record) {
  if (state.voiceStreaming) return prepareStreamedSpeech(record);
  return prepareWavSpeech(record);
}

function applyVoiceHeaders(record, response) {
  record.voiceProvider = response.headers.get("x-amora-tts-provider") || "unknown";
  record.voiceModel = response.headers.get("x-amora-tts-model") || "unknown";
  record.voiceFallback = response.headers.get("x-amora-tts-fallback") || "";
  record.audioPostprocessing = response.headers.get("x-amora-audio-postprocessing") || "unknown";
}

async function prepareWavSpeech(record) {
  const timer = setTimeout(() => record.controller.abort("voice_timeout"), VOICE_FETCH_TIMEOUT_MS);
  try {
    setVoiceStatus("preparando voz da Amora…");
    const response = await apiBinary(
      `/v1/web/voice/utterances/${encodeURIComponent(record.item.id)}/audio`,
      { signal: record.controller.signal },
    );
    if (record.epoch !== state.speechEpoch || record.interrupted) return;
    const rtf = response.headers.get("x-amora-rtf");
    applyVoiceHeaders(record, response);
    record.audioBuffer = await voicePlayer.prepare(response,
      () => record.epoch === state.speechEpoch && !record.interrupted && !state.voiceMuted);
    if (record.epoch !== state.speechEpoch || record.interrupted || state.voiceMuted) return;
    record.status = "ready";
    if (rtf) {
      const value = Number(rtf);
      $("diagRtf").textContent = Number.isFinite(value) ? value.toFixed(3) : rtf;
    }
    const fallback = record.voiceFallback ? " · fallback" : "";
    setVoiceStatus(`áudio pronto · ${record.voiceProvider}${fallback} · DSP ${record.audioPostprocessing}`);
    pumpPlayback();
  } catch (error) {
    if (record.interrupted || record.epoch !== state.speechEpoch) return;
    record.status = "failed";
    await failVoiceTransport(record, error?.name === "AbortError"
      ? new Error("tempo limite do worker de voz") : error);
  } finally {
    clearTimeout(timer);
    if (state.preparingRecord === record) state.preparingRecord = null;
    pumpPreparation();
  }
}

function pcm16leToFloat32(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength % 2) throw new Error("PCM16_ALIGNMENT_INVALID");
  const output = new Float32Array(bytes.byteLength / 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < output.length; i += 1) {
    const sample = view.getInt16(i * 2, true);
    output[i] = sample < 0 ? sample / 32768 : sample / 32767;
  }
  return output;
}

async function waitForStreamCapacity(player, record) {
  while (!record.interrupted && record.epoch === state.speechEpoch
      && player.state().bufferedSeconds > STREAM_BACKPRESSURE_HIGH_SECONDS) {
    await new Promise(resolve => setTimeout(resolve, 25));
    if (player.state().bufferedSeconds <= STREAM_BACKPRESSURE_LOW_SECONDS) break;
  }
}

async function markStreamStarted(record) {
  if (record.streamStarted || record.interrupted || record.epoch !== state.speechEpoch) return;
  record.streamStarted = true;
  record.status = "playing";
  if (state.lastSpeechEndAt) {
    $("diagGap").textContent = `${Math.round(performance.now() - state.lastSpeechEndAt)} ms`;
  }
  updateQueueDiag();
  const fallback = record.voiceFallback ? " · fallback" : "";
  setVoiceStatus(`falando em stream · ${record.voiceProvider || "Amora"}${fallback} · DSP ${record.audioPostprocessing || "unknown"}`);
  api(`/v1/conversation/utterances/${encodeURIComponent(record.item.id)}/started`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => setVoiceStatus("falha ao confirmar início da fala"));
}

async function prepareStreamedSpeech(record) {
  const timer = setTimeout(() => record.controller.abort("voice_timeout"), VOICE_FETCH_TIMEOUT_MS);
  try {
    setVoiceStatus("abrindo stream da Amora…");
    const response = await apiBinary(
      `/v1/web/voice/utterances/${encodeURIComponent(record.item.id)}/stream`,
      { signal: record.controller.signal },
    );
    if (record.epoch !== state.speechEpoch || record.interrupted) return;
    if (!response.body?.getReader) {
      state.voiceStreaming = false;
      try { await response.body?.cancel?.(); } catch (_) {}
      record.status = "preparing";
      return prepareWavSpeech(record);
    }

    applyVoiceHeaders(record, response);
    const sampleRate = Number(response.headers.get("x-amora-sample-rate"));
    const channels = Number(response.headers.get("x-amora-channels") || "1");
    const sessionId = response.headers.get("x-amora-stream-session");
    const format = response.headers.get("x-amora-audio-format");
    if (!Number.isInteger(sampleRate) || !Number.isInteger(channels) || !sessionId || format !== "pcm_s16le") {
      throw new Error("metadados de stream PCM inválidos");
    }

    const context = getPlaybackContext();
    if (!state.playbackUnlocked || context.state !== "running") {
      throw new Error("player Web Audio não está liberado");
    }
    const identity = {
      sessionId,
      turnId: cognitiveTurnId(record.item) || record.item.id,
      utteranceId: record.item.id,
    };
    let sequence = 0;
    const player = new PcmStreamPlayer(context, identity, {
      minBufferSeconds: 0.06,
      maxBufferSeconds: 2.0,
      onState: (streamState) => {
        if (streamState.status === "playing") markStreamStarted(record);
        if (streamState.status === "underrun") {
          setVoiceStatus(`stream recuperando · ${record.voiceProvider || "Amora"} · underruns ${streamState.underruns}`);
        }
      },
      onEnded: () => {
        if (record.interrupted || record.epoch !== state.speechEpoch) return;
        finishStreamedRecord(record);
      },
    });
    record.streamPlayer = player;
    state.activeRecord = record;
    updateQueueDiag();

    const reader = response.body.getReader();
    const bytesPerFrame = 2 * channels;
    let carry = new Uint8Array(0);
    let receivedAudio = false;
    while (true) {
      await waitForStreamCapacity(player, record);
      if (record.interrupted || record.epoch !== state.speechEpoch || state.voiceMuted) {
        try { await reader.cancel("voice_interrupted"); } catch (_) {}
        return;
      }
      const { value, done } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      const incoming = value instanceof Uint8Array ? value : new Uint8Array(value);
      const joined = new Uint8Array(carry.byteLength + incoming.byteLength);
      joined.set(carry, 0);
      joined.set(incoming, carry.byteLength);
      const usable = joined.byteLength - (joined.byteLength % bytesPerFrame);
      if (!usable) {
        carry = joined;
        continue;
      }
      const block = joined.slice(0, usable);
      carry = joined.slice(usable);
      player.push({
        ...identity,
        sequence: sequence++,
        sampleRate,
        channels,
        samples: pcm16leToFloat32(block),
        final: false,
      });
      receivedAudio = true;
    }
    if (carry.byteLength) throw new Error("stream PCM terminou desalinhado");
    if (!receivedAudio) throw new Error("stream PCM terminou sem áudio");
    player.push({
      ...identity,
      sequence: sequence++,
      sampleRate,
      channels,
      samples: new Float32Array(0),
      final: true,
    });
    $("diagRtf").textContent = "stream";
  } catch (error) {
    if (record.interrupted || record.epoch !== state.speechEpoch) return;
    record.status = "failed";
    record.streamPlayer?.stop();
    await failVoiceTransport(record, error?.name === "AbortError"
      ? new Error("tempo limite do stream de voz") : error);
  } finally {
    clearTimeout(timer);
    if (state.preparingRecord === record) state.preparingRecord = null;
    pumpPreparation();
  }
}

async function failVoiceTransport(record, error) {
  if (!state.voiceErrorShown) {
    state.voiceErrorShown = true;
    addMessage("system", `Voz indisponível: ${error.message}. Texto preservado; verifique o backend/worker configurado.`);
  }
  setVoiceStatus("voz indisponível · texto preservado");
  await interruptVoice("web_local_voice_error", record.item, true);
}

function pumpPlayback() {
  if (state.voiceStreaming || state.voiceMuted || state.activeRecord || state.userSpeaking) return;
  const record = state.speechQueue[0];
  if (!record || record.epoch !== state.speechEpoch || record.status !== "ready") return;
  const context = getPlaybackContext();
  if (!state.playbackUnlocked || context.state !== "running") {
    setVoiceStatus("toque em Enviar ou Microfone para liberar áudio");
    return;
  }

  record.status = "playing";
  state.activeRecord = record;
  if (state.lastSpeechEndAt) {
    $("diagGap").textContent = `${Math.round(performance.now() - state.lastSpeechEndAt)} ms`;
  }
  updateQueueDiag();
  const fallback = record.voiceFallback ? " · fallback" : "";
  setVoiceStatus(`falando · ${record.voiceProvider || "Amora"}${fallback} · DSP ${record.audioPostprocessing || "unknown"}`);

  state.activeSource = voicePlayer.play(record.audioBuffer, () => {
    if (record.interrupted || record.epoch !== state.speechEpoch) return;
    finishPlayedRecord(record);
  });
  api(`/v1/conversation/utterances/${encodeURIComponent(record.item.id)}/started`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => setVoiceStatus("falha ao confirmar início da fala"));
}

function settlePlayedRecord(record) {
  if (state.activeRecord === record) state.activeRecord = null;
  state.activeSource = null;
  state.lastSpeechEndAt = performance.now();
  const index = state.speechQueue.indexOf(record);
  if (index >= 0) state.speechQueue.splice(index, 1);
  updateQueueDiag();
  const fallback = record.voiceFallback ? " · fallback" : "";
  setVoiceStatus(`pronta · última voz ${record.voiceProvider || "Amora"}${fallback} · DSP ${record.audioPostprocessing || "unknown"}`);

  api(`/v1/conversation/utterances/${encodeURIComponent(record.item.id)}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spoken: true }),
  }).then(() => state.claimed.delete(record.item.id))
    .catch(() => setVoiceStatus("falha ao confirmar entrega da fala"));

  pumpPlayback();
  pumpPreparation();
  fillSpeechQueue();
}

function finishPlayedRecord(record) {
  if (state.activeRecord !== record) return;
  settlePlayedRecord(record);
}

function finishStreamedRecord(record) {
  if (state.activeRecord !== record) return;
  record.streamPlayer = null;
  settlePlayedRecord(record);
}

async function fillSpeechQueue() {
  if (!state.token || state.fillBusy || state.userSpeaking) return;
  state.fillBusy = true;
  const claimEpoch = state.speechEpoch;
  try {
    // Bound draining when muted while still settling remote leases normally.
    let claims = 0;
    while (state.speechQueue.length < MAX_SPEECH_QUEUE && claims++ < 12) {
      const last = state.speechQueue.at(-1)?.item || state.activeRecord?.item;
      const sameTurn = cognitiveTurnId(last);
      const item = await claimNext(sameTurn);
      if (state.queueError && $("chatNotice").textContent === state.queueError) $("chatNotice").textContent = "";
      state.queueError = "";
      updateQueueDiag();
      if (!item) break;
      queueLocalSpeech(item, claimEpoch);
      if (claimEpoch !== state.speechEpoch) break;
    }
  } catch (error) {
    if (error.status === 401) {
      state.token = "";
      browserStore.forgetPairing();
      pairPanel.classList.remove("hidden");
      appPanel.classList.add("hidden");
    } else {
      const diagnostic = `fila/resposta indisponível: ${error.status ? `HTTP ${error.status}` : "falha de conexão"}`;
      if (state.queueError !== diagnostic) addMessage("system", diagnostic);
      state.queueError = diagnostic;
      updateQueueDiag();
    }
  } finally {
    state.fillBusy = false;
  }
}

function suppressTurn(item) {
  const turn = cognitiveTurnId(item);
  if (!turn) return;
  state.suppressedTurns.add(turn);
  while (state.suppressedTurns.size > 512) state.suppressedTurns.delete(state.suppressedTurns.values().next().value);
}

async function cancelVoiceItem(item, reason, speechOnly = true) {
  suppressTurn(item);
  if (!state.token || state.cancelling.has(item.id)) return;
  // Keep failed cancellations for the next poll; never silently lose a lease.
  state.pendingCancels.set(item.id, { item, reason, speechOnly });
  state.cancelling.add(item.id);
  try {
    await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason, speech_only: speechOnly }),
      keepalive: true,
    });
    state.pendingCancels.delete(item.id);
    state.claimed.delete(item.id);
  } catch (error) {
    setVoiceStatus(`cancelamento vocal pendente: ${error.status ? `HTTP ${error.status}` : "falha de conexão"}`);
  } finally {
    state.cancelling.delete(item.id);
  }
}

async function interruptVoice(reason = "web_barge_in", explicitTarget = null, speechOnly = false) {
  const targets = new Map(state.claimed);
  if (explicitTarget) targets.set(explicitTarget.id, explicitTarget);
  state.speechEpoch += 1;
  for (const record of state.speechQueue) {
    record.interrupted = true;
    try { record.controller?.abort(reason); } catch (_) {}
    try { record.streamPlayer?.stop(); } catch (_) {}
  }
  if (state.activeRecord) {
    state.activeRecord.interrupted = true;
    try { state.activeRecord.streamPlayer?.stop(); } catch (_) {}
  }
  voicePlayer.stop();
  state.activeSource = null;
  state.activeRecord = null;
  state.preparingRecord = null;
  state.speechQueue = [];
  state.lastSpeechEndAt = performance.now();
  updateQueueDiag();
  await Promise.all([...targets.values()].map(item => cancelVoiceItem(item, reason, speechOnly)));
}

$("stopVoice").addEventListener("click", async () => {
  await interruptVoice("web_manual_stop", null, true);
});

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return Math.sqrt(sum / Math.max(1, samples.length));
}

function pushPreRoll(chunk, chunkMs) {
  state.preRoll.push(chunk.slice());
  state.preRollMs += chunkMs;
  while (state.preRollMs > 280 && state.preRoll.length > 1) {
    const removed = state.preRoll.shift();
    state.preRollMs -= removed.length / state.audioContext.sampleRate * 1000;
  }
}

function beginUserSpeech() {
  state.userSpeaking = true;
  state.speechParts = state.preRoll.map((part) => part.slice());
  state.speechMs = state.preRollMs;
  state.silenceMs = 0;
  state.aboveMs = 0;
  setMicStatus("falando…");
  $("micButton").classList.add("live");
  if (state.activeRecord || state.speechQueue.length) {
    interruptVoice("web_barge_in", null, false);
  }
}

function handlePcm(chunk) {
  if (!state.micEnabled || !chunk?.length) return;
  const value = rms(chunk);
  const chunkMs = chunk.length / state.audioContext.sampleRate * 1000;
  const outputActive = Boolean(state.activeRecord || state.speechQueue.length);
  const startThreshold = Math.max(outputActive ? 0.014 : 0.012, state.noiseFloor * (outputActive ? 2.2 : 2.0));

  if (!state.userSpeaking) {
    pushPreRoll(chunk, chunkMs);
    if (!outputActive && value < startThreshold) {
      state.noiseFloor = Math.max(0.0025, Math.min(0.03, state.noiseFloor * 0.97 + value * 0.03));
    }
    state.aboveMs = value >= startThreshold ? state.aboveMs + chunkMs : Math.max(0, state.aboveMs - chunkMs * 1.5);
    if (state.aboveMs >= 90 && !state.sendingAudio) beginUserSpeech();
    return;
  }

  state.speechParts.push(chunk.slice());
  state.speechMs += chunkMs;
  const endThreshold = Math.max(0.008, state.noiseFloor * 1.35);
  state.silenceMs = value < endThreshold ? state.silenceMs + chunkMs : 0;
  if ((state.silenceMs >= 520 && state.speechMs >= 260) || state.speechMs >= 10000) {
    finishUserSpeech();
  }
}

function concatFloat(parts) {
  const length = parts.reduce((sum, part) => sum + part.length, 0);
  const output = new Float32Array(length);
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.length;
  }
  return output;
}

function resampleLinear(source, inputRate, outputRate = 16000) {
  if (inputRate === outputRate) return source;
  const ratio = inputRate / outputRate;
  const length = Math.max(1, Math.floor(source.length / ratio));
  const output = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(source.length - 1, left + 1);
    const mix = position - left;
    output[i] = source[left] * (1 - mix) + source[right] * mix;
  }
  return output;
}

function wavBlob(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeAscii = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

async function finishUserSpeech() {
  if (!state.userSpeaking) return;
  const parts = state.speechParts;
  state.userSpeaking = false;
  state.speechParts = [];
  state.speechMs = 0;
  state.silenceMs = 0;
  state.preRoll = [];
  state.preRollMs = 0;
  $("micButton").classList.remove("live");
  setMicStatus("transcrevendo…");

  const source = concatFloat(parts);
  const samples = resampleLinear(source, state.audioContext.sampleRate, 16000);
  if (samples.length < 16000 * 0.20) {
    setMicStatus("ouvindo");
    return;
  }

  state.sendingAudio = true;
  state.lastTurnStartedAt = performance.now();
  try {
    const params = new URLSearchParams({
      language: "pt",
      speaker_id: "PERSON_BK",
      speaker_confidence: "0.80",
      speech_device_id: "WEB_SAFARI",
      privacy_scope: "PERSONAL",
      remote_processing_allowed: remoteToggle.checked ? "true" : "false",
      test_mode: testToggle.checked ? "true" : "false",
    });
    const result = await api(`/v1/audio/transcribe?${params}`, {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: wavBlob(samples),
    });
    if (result.accepted && result.transcript?.text) {
      addMessage("user", result.transcript.text);
      $("diagTurn").textContent = `${Math.round(performance.now() - state.lastTurnStartedAt)} ms`;
      await fillSpeechQueue();
    } else {
      addMessage("system", "Fala rejeitada pelo VAD/STT.");
    }
  } catch (error) {
    addMessage("system", `STT/turno falhou: ${error.message}`);
  } finally {
    state.sendingAudio = false;
    setMicStatus("ouvindo");
  }
}

async function enableMicrophone() {
  if (state.micEnabled) return;
  if (!window.isSecureContext) {
    throw new Error("O Safari exige HTTPS para liberar o microfone. Abra a URL https do tunnel.");
  }
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("getUserMedia não disponível neste navegador");

  await unlockPlayback();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  const context = new AudioContextCtor({ latencyHint: "interactive" });
  await context.resume();
  const source = context.createMediaStreamSource(stream);
  let captureNode;

  if (context.audioWorklet?.addModule) {
    await context.audioWorklet.addModule(new URL("./pcm-capture-worklet.js", import.meta.url));
    captureNode = new AudioWorkletNode(context, "amora-pcm-capture");
    captureNode.port.onmessage = (event) => handlePcm(event.data);
  } else {
    captureNode = context.createScriptProcessor(1024, 1, 1);
    captureNode.onaudioprocess = (event) => handlePcm(event.inputBuffer.getChannelData(0).slice());
  }

  const mute = context.createGain();
  mute.gain.value = 0;
  source.connect(captureNode);
  captureNode.connect(mute);
  mute.connect(context.destination);

  state.mediaStream = stream;
  state.audioContext = context;
  state.captureNode = captureNode;
  state.muteNode = mute;
  state.micEnabled = true;
  $("micButton").classList.add("live");
  $("micLabel").textContent = "Microfone ativo";
  setMicStatus("ouvindo");
}

async function disableMicrophone() {
  if (!state.micEnabled) return;
  state.micEnabled = false;
  state.mediaStream?.getTracks?.().forEach((track) => track.stop());
  try { state.captureNode?.disconnect?.(); } catch (_) {}
  try { state.muteNode?.disconnect?.(); } catch (_) {}
  try { await state.audioContext?.close?.(); } catch (_) {}
  state.mediaStream = null;
  state.captureNode = null;
  state.muteNode = null;
  state.audioContext = null;
  state.preRoll = [];
  state.preRollMs = 0;
  state.userSpeaking = false;
  $("micButton").classList.remove("live");
  $("micLabel").textContent = "Ativar microfone";
  setMicStatus("microfone desligado");
}

$("micButton").addEventListener("click", async () => {
  try {
    await unlockPlayback();
    if (state.micEnabled) await disableMicrophone();
    else await enableMicrophone();
  } catch (error) {
    addMessage("system", `Microfone: ${error.message}`);
    setMicStatus("erro");
  }
});

window.addEventListener("pagehide", () => {
  interruptVoice("web_pagehide", null, true);
  disableMicrophone();
});

document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible") {
    if (state.audioContext?.state === "suspended") {
      try { await state.audioContext.resume(); } catch (_) {}
    }
    if (state.playbackUnlocked && state.playbackContext?.state === "suspended") {
      try { await state.playbackContext.resume(); } catch (_) {}
    }
    pumpPlayback();
    pumpPreparation();
  }
});

setInterval(() => {
  if (!document.hidden) {
    for (const { item, reason, speechOnly } of state.pendingCancels.values()) cancelVoiceItem(item, reason, speechOnly);
    health();
    fillSpeechQueue();
    pumpPlayback();
    pumpPreparation();
  }
}, 900);

updateQueueDiag();
showCorrectSurface();
