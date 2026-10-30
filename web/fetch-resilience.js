const nativeFetch = window.fetch.bind(window);

let voiceStreamDowngraded = false;
let activeTurnRequests = 0;
let queueEmptyUntil = 0;
let lastHealthBody = null;
let lastHealthAt = 0;

const HEALTH_CACHE_MS = 4000;
const HEALTH_STALE_GRACE_MS = 12000;
const EMPTY_QUEUE_CACHE_MS = 2000;

function asUrl(input) {
  const raw = typeof input === "string" || input instanceof URL ? input : input?.url;
  return new URL(raw, window.location.href);
}

function methodOf(init) {
  return String(init?.method || "GET").toUpperCase();
}

function isVoiceStream(url) {
  return /^\/v1\/web\/voice\/utterances\/[^/]+\/stream$/.test(url.pathname);
}

function isWebTurn(url) {
  return url.pathname === "/v1/web/utterance";
}

function isTurnRequest(url, init) {
  if (methodOf(init) !== "POST") return false;
  return url.pathname === "/v1/web/utterance" || url.pathname === "/v1/audio/transcribe";
}

function isHealth(url, init) {
  return methodOf(init) === "GET" && url.pathname === "/health";
}

function isSafariQueueNext(url, init) {
  return methodOf(init) === "GET"
    && url.pathname === "/v1/conversation/next"
    && url.searchParams.get("device_id") === "WEB_SAFARI";
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function setVoiceDiagnostic(text) {
  const node = document.getElementById("diagVoice");
  if (node) node.textContent = text;
}

function numericHeader(response, name) {
  const raw = response.headers.get(name);
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function observeTurnTiming(response) {
  if (!response.ok) return;
  const trace = {
    version: response.headers.get("X-Amora-Latency-Trace-Version"),
    context: numericHeader(response, "X-Amora-Latency-Context-Ms"),
    provider: numericHeader(response, "X-Amora-Latency-Provider-Total-Ms"),
    surface: numericHeader(response, "X-Amora-Latency-Provider-Surface-Ms"),
    headers: numericHeader(response, "X-Amora-Latency-Provider-Headers-Ms"),
    firstContent: numericHeader(response, "X-Amora-Latency-Provider-First-Content-Ms"),
    firstPreview: numericHeader(response, "X-Amora-Latency-Provider-First-Preview-Ms"),
    previewFromRequest: numericHeader(response, "X-Amora-Latency-First-Preview-Ms"),
    parse: numericHeader(response, "X-Amora-Latency-Provider-Parse-Ms"),
    specialist: numericHeader(response, "X-Amora-Latency-Specialist-Ms"),
    remoteSql: numericHeader(response, "X-Amora-Latency-Remote-Sql-Ms"),
    remoteSqlCalls: numericHeader(response, "X-Amora-Latency-Remote-Sql-Calls"),
    remoteSqlMax: numericHeader(response, "X-Amora-Latency-Remote-Sql-Max-Ms"),
  };

  response.clone().json().then((body) => {
    const timing = body?.timing;
    if (!timing || typeof timing.total_ms !== "number") return;
    const embedded = timing.surgical || {};
    const value = (headerValue, key) => headerValue ?? (Number.isFinite(Number(embedded[key])) ? Number(embedded[key]) : null);
    trace.context = value(trace.context, "context_compile_ms");
    trace.provider = value(trace.provider, "provider_total_ms");
    trace.surface = value(trace.surface, "provider_surface_ms");
    trace.headers = value(trace.headers, "provider_headers_ms");
    trace.firstContent = value(trace.firstContent, "provider_first_content_ms");
    trace.firstPreview = value(trace.firstPreview, "provider_first_preview_ms");
    trace.previewFromRequest = value(trace.previewFromRequest, "first_preview_from_request_ms");
    trace.parse = value(trace.parse, "provider_parse_ms");
    trace.specialist = value(trace.specialist, "specialist_ms");
    trace.remoteSql = value(trace.remoteSql, "remote_sql_ms");
    trace.remoteSqlCalls = value(trace.remoteSqlCalls, "remote_sql_calls");
    trace.remoteSqlMax = value(trace.remoteSqlMax, "remote_sql_max_ms");

    setTimeout(() => {
      const node = document.getElementById("diagTurn");
      if (!node) return;
      const total = Math.round(timing.total_ms);
      const cognition = typeof timing.cognition_ms === "number" ? Math.round(timing.cognition_ms) : null;
      const social = typeof timing.language_social_ms === "number" ? Math.round(timing.language_social_ms) : null;
      const local = typeof timing.local_overhead_ms === "number" ? Math.round(timing.local_overhead_ms) : null;
      const compact = [`${total} ms`];
      if (cognition !== null) compact.push(`cog ${cognition}`);
      if (trace.provider !== null) compact.push(`prov ${Math.round(trace.provider)}`);
      if (trace.firstContent !== null) compact.push(`ttft ${Math.round(trace.firstContent)}`);
      if (trace.context !== null) compact.push(`ctx ${Math.round(trace.context)}`);
      if (trace.remoteSql !== null) {
        const calls = trace.remoteSqlCalls !== null ? `/${Math.round(trace.remoteSqlCalls)}` : "";
        compact.push(`db ${Math.round(trace.remoteSql)}${calls}`);
      }
      if (trace.provider === null && trace.context === null && trace.remoteSql === null) {
        compact.push(`trace ${trace.version || "n/a"}`);
      }
      if (social !== null) compact.push(`social ${social}`);
      if (local !== null) compact.push(`local ${local}`);
      node.textContent = compact.join(" · ");
      node.title = [
        `total=${timing.total_ms}ms`,
        `language_social=${timing.language_social_ms}ms`,
        `conversation=${timing.conversation_ms}ms`,
        `cognition=${timing.cognition_ms}ms`,
        `local_overhead=${timing.local_overhead_ms}ms`,
        `trace_version=${trace.version ?? "n/a"}`,
        `context_compile=${trace.context ?? "n/a"}ms`,
        `provider_total=${trace.provider ?? "n/a"}ms`,
        `provider_surface=${trace.surface ?? "n/a"}ms`,
        `provider_headers=${trace.headers ?? "n/a"}ms`,
        `provider_first_content=${trace.firstContent ?? "n/a"}ms`,
        `provider_first_preview=${trace.firstPreview ?? "n/a"}ms`,
        `first_preview_from_request=${trace.previewFromRequest ?? "n/a"}ms`,
        `provider_parse=${trace.parse ?? "n/a"}ms`,
        `specialist=${trace.specialist ?? "n/a"}ms`,
        `remote_sql=${trace.remoteSql ?? "n/a"}ms`,
        `remote_sql_calls=${trace.remoteSqlCalls ?? "n/a"}`,
        `remote_sql_max=${trace.remoteSqlMax ?? "n/a"}ms`,
      ].join(" | ");
    }, 0);
  }).catch(() => {});
}

async function resilientHealth(input, init) {
  const now = Date.now();
  if (lastHealthBody && now - lastHealthAt < HEALTH_CACHE_MS) return jsonResponse(lastHealthBody);
  try {
    const response = await nativeFetch(input, init);
    if (response.ok) {
      try {
        lastHealthBody = await response.clone().json();
        lastHealthAt = Date.now();
      } catch (_) {}
    }
    return response;
  } catch (error) {
    if (lastHealthBody && now - lastHealthAt < HEALTH_STALE_GRACE_MS) return jsonResponse(lastHealthBody);
    throw error;
  }
}

async function resilientQueueNext(input, init) {
  if (activeTurnRequests > 0 || Date.now() < queueEmptyUntil) {
    return jsonResponse({ utterance: null });
  }
  const response = await nativeFetch(input, init);
  if (response.ok) {
    try {
      const body = await response.clone().json();
      if (!body?.utterance) queueEmptyUntil = Date.now() + EMPTY_QUEUE_CACHE_MS;
      else queueEmptyUntil = 0;
    } catch (_) {}
  }
  return response;
}

function readAscii(bytes, offset, length) {
  let text = "";
  for (let index = 0; index < length; index += 1) {
    text += String.fromCharCode(bytes[offset + index]);
  }
  return text;
}

function parsePcmWav(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 44) {
    throw new Error("WAV de recuperação inválido");
  }
  if (readAscii(bytes, 0, 4) !== "RIFF" || readAscii(bytes, 8, 4) !== "WAVE") {
    throw new Error("WAV de recuperação sem RIFF/WAVE");
  }

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 12;
  let format = null;
  let data = null;
  while (offset + 8 <= bytes.byteLength) {
    const id = readAscii(bytes, offset, 4);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (body + size > bytes.byteLength) break;
    if (id === "fmt " && size >= 16) {
      format = {
        encoding: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bitsPerSample: view.getUint16(body + 14, true),
      };
    } else if (id === "data") {
      data = bytes.slice(body, body + size);
    }
    offset = body + size + (size % 2);
  }

  if (!format || !data?.byteLength) throw new Error("WAV de recuperação sem fmt/data");
  if (format.encoding !== 1 || format.bitsPerSample !== 16) {
    throw new Error("WAV de recuperação precisa ser PCM16");
  }
  if (format.channels < 1 || format.channels > 2 || !format.sampleRate) {
    throw new Error("metadados WAV de recuperação inválidos");
  }
  return { ...format, pcm: data };
}

function chunkedByteStream(bytes, chunkBytes = 8192) {
  let offset = 0;
  return new ReadableStream({
    pull(controller) {
      if (offset >= bytes.byteLength) {
        controller.close();
        return;
      }
      const end = Math.min(bytes.byteLength, offset + chunkBytes);
      controller.enqueue(bytes.slice(offset, end));
      offset = end;
    },
  });
}

async function wavFallback(url, init, cause) {
  const audioUrl = new URL(url.href);
  audioUrl.pathname = audioUrl.pathname.replace(/\/stream$/, "/audio");
  const response = await nativeFetch(audioUrl.href, { ...init, cache: "no-store" });
  if (!response.ok) return response;

  const parsed = parsePcmWav(new Uint8Array(await response.arrayBuffer()));
  const headers = new Headers(response.headers);
  headers.set("Content-Type", "application/octet-stream");
  headers.set("X-Amora-Audio-Format", "pcm_s16le");
  headers.set("X-Amora-Sample-Rate", String(parsed.sampleRate));
  headers.set("X-Amora-Channels", String(parsed.channels));
  headers.set("X-Amora-Streaming", "0");
  headers.set("X-Amora-Stream-Session", globalThis.crypto?.randomUUID?.() || `wav-${Date.now()}`);
  headers.set("X-Amora-Transport-Fallback", "wav");
  headers.delete("Content-Length");

  const suffix = cause?.message ? ` · ${cause.message}` : "";
  setVoiceDiagnostic(`stream indisponível · recuperação WAV ativa${suffix}`);
  return new Response(chunkedByteStream(parsed.pcm), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function preflightVoiceStream(response, url, init) {
  if (!response.ok || !response.body?.getReader) return response;
  const reader = response.body.getReader();
  let first;
  try {
    first = await reader.read();
  } catch (error) {
    try { await reader.cancel("preflight_failed"); } catch (_) {}
    if (init?.signal?.aborted) throw error;
    voiceStreamDowngraded = true;
    return wavFallback(url, init, error);
  }

  if (first.done || !first.value?.byteLength) {
    try { await reader.cancel("empty_stream"); } catch (_) {}
    voiceStreamDowngraded = true;
    return wavFallback(url, init, new Error("stream terminou antes do primeiro PCM"));
  }

  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(first.value);
    },
    async pull(controller) {
      try {
        const next = await reader.read();
        if (next.done) controller.close();
        else if (next.value?.byteLength) controller.enqueue(next.value);
      } catch (error) {
        // Once PCM reached Safari we intentionally do not splice a second render
        // into the same sentence. The normal player error path owns interruption.
        controller.error(error);
      }
    },
    async cancel(reason) {
      try { await reader.cancel(reason); } catch (_) {}
    },
  });

  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers: new Headers(response.headers),
  });
}

window.fetch = async function amoraResilientFetch(input, init = {}) {
  const url = asUrl(input);

  if (isVoiceStream(url) && voiceStreamDowngraded && !init?.signal?.aborted) {
    return wavFallback(url, init, new Error("stream desativado após falha anterior"));
  }
  if (isHealth(url, init)) return resilientHealth(input, init);
  if (isSafariQueueNext(url, init)) return resilientQueueNext(input, init);

  const turnRequest = isTurnRequest(url, init);
  if (turnRequest) activeTurnRequests += 1;
  try {
    const response = await nativeFetch(input, init);
    if (isWebTurn(url)) observeTurnTiming(response);
    if (isVoiceStream(url)) return preflightVoiceStream(response, url, init);
    return response;
  } catch (error) {
    if (isVoiceStream(url) && !init?.signal?.aborted) {
      voiceStreamDowngraded = true;
      return wavFallback(url, init, error);
    }
    throw error;
  } finally {
    if (turnRequest) activeTurnRequests = Math.max(0, activeTurnRequests - 1);
  }
};

window.__AMORA_FETCH_RESILIENCE__ = {
  get voiceStreamDowngraded() { return voiceStreamDowngraded; },
  get activeTurnRequests() { return activeTurnRequests; },
};
