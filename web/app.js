const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "amora.web.token.v1";
const REMOTE_KEY = "amora.web.remote.v1";
const TEST_KEY = "amora.web.test.v1";
const MAX_SPEECH_QUEUE = 3;

const state = {
  token: localStorage.getItem(TOKEN_KEY) || "",
  fillBusy: false,
  speechQueue: [],
  activeUtterance: null,
  speechEpoch: 0,
  lastSpeechEndAt: 0,
  lastTurnStartedAt: 0,
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

function updateQueueDiag() {
  $("diagQueue").textContent = String(state.speechQueue.length);
  $("stopVoice").disabled = state.speechQueue.length === 0 && !state.activeUtterance;
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

async function health() {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const data = await response.json();
    setCoreStatus(data.status === "ok" ? `online · ${data.version || "Core"}` : data.status, data.status === "ok" ? "ok" : "bad");
    return data.status === "ok";
  } catch (error) {
    setCoreStatus("offline", "bad");
    return false;
  }
}

async function validatePairing() {
  if (!state.token) return false;
  try {
    const data = await api("/v1/web/state");
    $("voiceLabel").textContent = data.canonical_voice
      ? "voz canônica disponível"
      : "voz temporária do Safari · não canônica";
    return true;
  } catch (error) {
    if (error.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      state.token = "";
    }
    return false;
  }
}

async function showCorrectSurface() {
  const ok = await health();
  const paired = ok && await validatePairing();
  pairPanel.classList.toggle("hidden", paired);
  appPanel.classList.toggle("hidden", !paired);
  if (paired) {
    addMessage("system", "Safari Lab conectado. Modo teste fica ligado por padrão para não consolidar estado durante probes.");
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
    await showCorrectSurface();
  } catch (error) {
    $("pairError").textContent = `Pareamento falhou: ${error.message}`;
  }
});

$("forgetPairing").addEventListener("click", () => {
  interruptVoice("web_unpair");
  localStorage.removeItem(TOKEN_KEY);
  state.token = "";
  location.reload();
});

async function submitText(text) {
  const clean = text.trim();
  if (!clean) return;
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

function choosePtBrVoice() {
  const voices = window.speechSynthesis?.getVoices?.() || [];
  return voices.find((voice) => voice.lang?.toLowerCase() === "pt-br")
    || voices.find((voice) => voice.lang?.toLowerCase().startsWith("pt"))
    || null;
}

function queueBrowserSpeech(item) {
  addMessage("amora", item.text);
  const record = { item, epoch: state.speechEpoch, started: false };
  state.speechQueue.push(record);
  updateQueueDiag();

  if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") {
    deliverTextOnly(record);
    return;
  }

  const speech = new SpeechSynthesisUtterance(item.text);
  speech.lang = "pt-BR";
  const voice = choosePtBrVoice();
  if (voice) speech.voice = voice;
  const style = item.style || {};
  const expression = style.expression || {};
  speech.rate = Math.max(0.82, Math.min(1.16, Number(expression.rate_scale ?? style.rate ?? 1)));
  speech.pitch = 1;
  speech.volume = Math.max(0, Math.min(1, Number(style.volume ?? 1)));

  speech.onstart = async () => {
    if (record.epoch !== state.speechEpoch) return;
    record.started = true;
    state.activeUtterance = item;
    if (state.lastSpeechEndAt) {
      $("diagGap").textContent = `${Math.round(performance.now() - state.lastSpeechEndAt)} ms`;
    }
    updateQueueDiag();
    try {
      await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/started`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (_) {}
  };

  speech.onend = () => finishSpokenRecord(record, true);
  speech.onerror = () => finishSpokenRecord(record, false);
  window.speechSynthesis.speak(speech);
}

async function deliverTextOnly(record) {
  const { item } = record;
  try {
    await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/started`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/ack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spoken: true }),
    });
  } catch (_) {}
  finishQueueRecord(record);
}

async function finishSpokenRecord(record, success) {
  if (record.epoch !== state.speechEpoch) return;
  const { item } = record;
  if (state.activeUtterance?.id === item.id) state.activeUtterance = null;
  state.lastSpeechEndAt = performance.now();
  try {
    if (success) {
      await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/ack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spoken: true }),
      });
    } else {
      await api(`/v1/conversation/utterances/${encodeURIComponent(item.id)}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "web_speech_transport_error" }),
      });
    }
  } catch (_) {}
  finishQueueRecord(record);
}

function finishQueueRecord(record) {
  const index = state.speechQueue.indexOf(record);
  if (index >= 0) state.speechQueue.splice(index, 1);
  updateQueueDiag();
  fillSpeechQueue();
}

async function fillSpeechQueue() {
  if (!state.token || state.fillBusy || state.userSpeaking) return;
  state.fillBusy = true;
  try {
    while (state.speechQueue.length < MAX_SPEECH_QUEUE) {
      const last = state.speechQueue.at(-1)?.item || state.activeUtterance;
      const sameTurn = cognitiveTurnId(last);
      const item = await claimNext(sameTurn);
      if (!item) break;
      queueBrowserSpeech(item);
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

async function interruptVoice(reason = "web_barge_in") {
  const target = state.activeUtterance || state.speechQueue[0]?.item || null;
  state.speechEpoch += 1;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  state.speechQueue = [];
  state.activeUtterance = null;
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

$("stopVoice").addEventListener("click", () => interruptVoice("web_manual_stop"));

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
  if (state.activeUtterance || state.speechQueue.length) {
    interruptVoice("web_barge_in");
  }
}

function handlePcm(chunk) {
  if (!state.micEnabled || !chunk?.length) return;
  const value = rms(chunk);
  const chunkMs = chunk.length / state.audioContext.sampleRate * 1000;
  const outputActive = Boolean(state.activeUtterance || state.speechQueue.length);
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
      privacy_scope: "PERSONAL",
      remote_processing_allowed: remoteToggle.checked ? "true" : "false",
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
    if (state.micEnabled) await disableMicrophone();
    else await enableMicrophone();
  } catch (error) {
    addMessage("system", `Microfone: ${error.message}`);
    setMicStatus("erro");
  }
});

window.addEventListener("pagehide", () => {
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  state.mediaStream?.getTracks?.().forEach((track) => track.stop());
});

document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && state.audioContext?.state === "suspended") {
    try { await state.audioContext.resume(); } catch (_) {}
  }
});

setInterval(() => {
  if (!document.hidden) {
    health();
    fillSpeechQueue();
  }
}, 900);

updateQueueDiag();
showCorrectSurface();
