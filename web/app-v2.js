const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "amora.web.token.v1";
const REMOTE_KEY = "amora.web.remote.v1";
const TEST_KEY = "amora.web.test.v1";
const MAX_SPEECH_QUEUE = 3;
const VOICE_FETCH_TIMEOUT_MS = 90000;

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  fillBusy: false,
  speechQueue: [],
  activeRecord: null,
  activeSource: null,
  speechEpoch: 0,
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

const pairPanel = $("pairPanel");
const appPanel = $("appPanel");
const remoteToggle = $("remoteToggle");
const testToggle = $("testToggle");
remoteToggle.checked = localStorage.getItem(REMOTE_KEY) !== "0";
testToggle.checked = localStorage.getItem(TEST_KEY) !== "0";
remoteToggle.addEventListener("change", () => localStorage.setItem(REMOTE_KEY, remoteToggle.checked ? "1" : "0"));
testToggle.addEventListener("change", () => localStorage.setItem(TEST_KEY, testToggle.checked ? "1" : "0"));

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
  $("diagQueue").textContent = String(state.speechQueue.length);
  $("stopVoice").disabled = state.speechQueue.length === 0 && !state.activeRecord;
}

function addMessage(role, text) {
  if (!text?.trim()) return;
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text.trim();
  $("messages").appendChild(node);
  requestAnimationFrame(() => {
    $("messages").scrollTop = $("messages").scrollHeight;
  });
}

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
    const local = data.local_voice || {};
    if (local.available) {
      $("voiceLabel").textContent = "Amora local · FINAL_B + Signature V1";
      setVoiceStatus(`pronta · ${local.num_steps || 5} steps`);
    } else {
      $("voiceLabel").textContent = "Amora local · setup necessário";
      setVoiceStatus("indisponível · rode setup-local-voice.sh");
    }
    return true;
  } catch (error) {
    if (error.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
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
  pairPanel.classList.toggle("hidden", paired);
  appPanel.classList.toggle("hidden", !paired);
  if (paired) {
    addMessage("system", "Safari Lab conectado. A voz agora é renderizada pelo Core local; modo teste fica ligado por padrão.");
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
    state.token = data.token;
    localStorage.setItem(TOKEN_KEY, state.token);
    $("pairCode").value = "";
    await unlockPlayback();
    await showCorrectSurface();
  } catch (error) {
    $("pairError").textContent = `Pareamento falhou: ${error.message}`;
  }
});

$("forgetPairing").addEventListener("click", async () => {
  await interruptVoice("web_unpair");
  localStorage.removeItem(TOKEN_KEY);
  state.token = "";
  location.reload();
});

async function submitText(text) {
  const clean = text.trim();
  if (!clean) return;
  await unlockPlayback();
  if (state.activeRecord || state.speechQueue.length) {
    await interruptVoice("web_text_barge_in");
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

function queueLocalSpeech(item) {
  addMessage("amora", item.text);
  const record = {
    item,
    epoch: state.speechEpoch,
    status: "preparing",
    audioBuffer: null,
    controller: new AbortController(),
    interrupted: false,
  };
  state.speechQueue.push(record);
  updateQueueDiag();
  prepareLocalSpeech(record);
}

async function prepareLocalSpeech(record) {
  const timer = setTimeout(() => record.controller.abort("voice_timeout"), VOICE_FETCH_TIMEOUT_MS);
  try {
    setVoiceStatus("sintetizando Amora local…");
    const response = await apiBinary(
      `/v1/web/voice/utterances/${encodeURIComponent(record.item.id)}/audio`,
      { signal: record.controller.signal },
    );
    if (record.epoch !== state.speechEpoch || record.interrupted) return;
    const rtf = response.headers.get("x-amora-rtf");
    const bytes = await response.arrayBuffer();
    const context = getPlaybackContext();
    record.audioBuffer = await context.decodeAudioData(bytes.slice(0));
    record.status = "ready";
    if (rtf) {
      const value = Number(rtf);
      $("diagRtf").textContent = Number.isFinite(value) ? value.toFixed(3) : rtf;
    }
    setVoiceStatus("áudio pronto");
    pumpPlayback();
  } catch (error) {
    if (record.interrupted || record.epoch !== state.speechEpoch || error?.name === "AbortError") return;
    record.status = "failed";
    await failVoiceTransport(record, error);
  } finally {
    clearTimeout(timer);
  }
}

async function failVoiceTransport(record, error) {
  if (!state.voiceErrorShown) {
    state.voiceErrorShown = true;
    addMessage("system", `Voz local indisponível: ${error.message}. Rode tools/web/setup-local-voice.sh no Termux e reinicie o Core.`);
  }
  setVoiceStatus("erro na voz local");
  await interruptVoice("web_local_voice_error", record.item);
}

function pumpPlayback() {
  if (state.activeRecord || state.userSpeaking) return;
  const record = state.speechQueue[0];
  if (!record || record.epoch !== state.speechEpoch || record.status !== "ready") return;
  const context = getPlaybackContext();
  if (!state.playbackUnlocked || context.state !== "running") {
    setVoiceStatus("toque em Enviar ou Microfone para liberar áudio");
    return;
  }

  const source = context.createBufferSource();
  source.buffer = record.audioBuffer;
  source.connect(context.destination);
  record.status = "playing";
  state.activeRecord = record;
  state.activeSource = source;
  if (state.lastSpeechEndAt) {
    $("diagGap").textContent = `${Math.round(performance.now() - state.lastSpeechEndAt)} ms`;
  }
  updateQueueDiag();
  setVoiceStatus("falando · Amora local");

  source.onended = () => {
    if (record.interrupted || record.epoch !== state.speechEpoch) return;
    finishPlayedRecord(record);
  };

  source.start();
  api(`/v1/conversation/utterances/${encodeURIComponent(record.item.id)}/started`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => {});
}

function finishPlayedRecord(record) {
  if (state.activeRecord !== record) return;
  state.activeRecord = null;
  state.activeSource = null;
  state.lastSpeechEndAt = performance.now();
  const index = state.speechQueue.indexOf(record);
  if (index >= 0) state.speechQueue.splice(index, 1);
  updateQueueDiag();
  setVoiceStatus("pronta");

  api(`/v1/conversation/utterances/${encodeURIComponent(record.item.id)}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spoken: true }),
  }).catch(() => {});

  pumpPlayback();
  fillSpeechQueue();
}

async function fillSpeechQueue() {
  if (!state.token || state.fillBusy || state.userSpeaking) return;
  state.fillBusy = true;
  try {
    while (state.speechQueue.length < MAX_SPEECH_QUEUE) {
      const last = state.speechQueue.at(-1)?.item || state.activeRecord?.item;
      const sameTurn = cognitiveTurnId(last);
      const item = await claimNext(sameTurn);
      if (!item) break;
      queueLocalSpeech(item);
    }
  } catch (error) {
    if (error.status === 401) {
      state.token = "";
      localStorage.removeItem(TOKEN_KEY);
      pairPanel.classList.remove("hidden");
      appPanel.classList.add("hidden");
    }
  } finally {
    state.fillBusy = false;
  }
}

async function interruptVoice(reason = "web_barge_in", explicitTarget = null) {
  const target = explicitTarget || state.activeRecord?.item || state.speechQueue[0]?.item || null;
  state.speechEpoch += 1;
  for (const record of state.speechQueue) {
    record.interrupted = true;
    try { record.controller?.abort(reason); } catch (_) {}
  }
  if (state.activeRecord) state.activeRecord.interrupted = true;
  try { state.activeSource?.stop?.(); } catch (_) {}
  state.activeSource = null;
  state.activeRecord = null;
  state.speechQueue = [];
  state.lastSpeechEndAt = performance.now();
  updateQueueDiag();
  if (!target || !state.token) return;
  try {
    await api(`/v1/conversation/utterances/${encodeURIComponent(target.id)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
  } catch (_) {}
}

$("stopVoice").addEventListener("click", async () => {
  await unlockPlayback();
  await interruptVoice("web_manual_stop");
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
    interruptVoice("web_barge_in");
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
  state.speechEpoch += 1;
  for (const record of state.speechQueue) {
    record.interrupted = true;
    try { record.controller?.abort("pagehide"); } catch (_) {}
  }
  try { state.activeSource?.stop?.(); } catch (_) {}
  state.mediaStream?.getTracks?.().forEach((track) => track.stop());
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
  }
});

setInterval(() => {
  if (!document.hidden) {
    health();
    fillSpeechQueue();
    pumpPlayback();
  }
}, 900);

updateQueueDiag();
showCorrectSurface();
