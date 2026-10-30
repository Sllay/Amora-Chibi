const nativeFetch = window.fetch.bind(window);

let voiceStreamDowngraded = false;

function asUrl(input) {
  const raw = typeof input === "string" || input instanceof URL ? input : input?.url;
  return new URL(raw, window.location.href);
}

function isVoiceStream(url) {
  return /^\/v1\/web\/voice\/utterances\/[^/]+\/stream$/.test(url.pathname);
}

function isWebTurn(url) {
  return url.pathname === "/v1/web/utterance";
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
      if (social !== null) compact.push(`social ${social}`);
      if (local !== null) compact.push(`local ${local}`);
      node.textContent = compact.join(" · ");
      node.title = [
        `total=${timing.total_ms}ms`,
        `language_social=${timing.language_social_ms}ms`,
        `conversation=${timing.conversation_ms}ms`,
        `cognition=${timing.cognition_ms}ms`,
        `local_overhead=${timing.local_overhead_ms}ms`,
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
  }
};

window.__AMORA_FETCH_RESILIENCE__ = {
  get voiceStreamDowngraded() { return voiceStreamDowngraded; },
};
