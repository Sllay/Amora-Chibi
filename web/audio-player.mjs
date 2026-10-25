// Platform audio only. No synthesis, prosody, identity or conversation ownership.
export class WavPlayer {
  constructor(getContext) {
    this.getContext = getContext;
    this.source = null;
  }

  async prepare(response, isCurrent = () => true) {
    const bytes = await response.arrayBuffer();
    if (!isCurrent()) throw new DOMException('Audio generation superseded', 'AbortError');
    return this.getContext().decodeAudioData(bytes);
  }

  play(buffer, onEnded) {
    this.stop();
    const context = this.getContext();
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    this.source = source;
    source.onended = () => {
      source.disconnect();
      if (this.source !== source) return;
      this.source = null;
      onEnded();
    };
    source.start();
    return source;
  }

  stop() {
    const source = this.source;
    this.source = null;
    if (!source) return;
    source.onended = null;
    try { source.stop(); } catch (_) {}
    source.disconnect();
  }
}

export class PcmStreamPlayer {
  // Scheduled PCM blocks are supported by Safari Web Audio. This bounded queue
  // is also the boundary for a future AudioWorklet/ring-buffer sink. It never
  // waits for a complete response or runs decodeAudioData. Not wired to TTS yet.
  constructor(context, identity, { minBufferSeconds = 0.06, maxBufferSeconds = 2, onState = () => {}, onEnded = () => {} } = {}) {
    if (!identity?.turnId || !identity?.utteranceId || !identity?.sessionId
        || !Number.isFinite(minBufferSeconds) || !Number.isFinite(maxBufferSeconds)
        || !(minBufferSeconds >= 0) || !(maxBufferSeconds > minBufferSeconds)) throw new Error('PCM_CONFIG_INVALID');
    this.context = context;
    this.identity = { ...identity };
    this.minBufferSeconds = minBufferSeconds;
    this.maxBufferSeconds = maxBufferSeconds;
    this.onState = onState;
    this.onEnded = onEnded;
    this.sequence = 0;
    this.format = null;
    this.pending = [];
    this.nodes = new Set();
    this.nextAt = context.currentTime;
    this.final = false;
    this.stopped = false;
    this.underruns = 0;
    this.status = 'buffering';
    this.epoch = 0;
  }

  state() {
    return { status: this.status, underruns: this.underruns,
      bufferedSeconds: this.pending.reduce((n, block) => n + block.frames / block.sampleRate, 0)
        + Math.max(0, this.nextAt - this.context.currentTime) };
  }

  report(status) {
    this.status = status;
    this.onState(this.state());
  }

  push(chunk) {
    if (this.stopped || this.final) throw new Error('PCM_STREAM_CLOSED');
    if (chunk.sequence !== this.sequence
        || ['sessionId', 'turnId', 'utteranceId'].some(key => chunk[key] !== this.identity[key])) throw new Error('PCM_STREAM_DISCONTINUITY');
    if (!Number.isInteger(chunk.sampleRate) || chunk.sampleRate < 8000 || chunk.sampleRate > 192000
        || ![1, 2].includes(chunk.channels) || typeof chunk.final !== 'boolean'
        || !(chunk.samples instanceof Float32Array) || chunk.samples.length % chunk.channels
        || chunk.samples.some(value => !Number.isFinite(value) || Math.abs(value) > 1)) throw new Error('PCM_FORMAT_INVALID');
    const format = `${chunk.sampleRate}:${chunk.channels}`;
    if (this.format !== null && this.format !== format) throw new Error('PCM_FORMAT_CHANGED');
    const frames = chunk.samples.length / chunk.channels;
    if (this.state().bufferedSeconds + frames / chunk.sampleRate > this.maxBufferSeconds) throw new Error('PCM_BUFFER_FULL');
    // Own the buffer; callers may reuse or mutate their transport chunk.
    this.pending.push({ samples: chunk.samples.slice(), sampleRate: chunk.sampleRate, channels: chunk.channels, frames });
    this.format = format;
    this.sequence++;
    this.final = chunk.final;
    if (this.status === 'playing' || this.final || this.state().bufferedSeconds >= this.minBufferSeconds) this.schedule();
    else this.report('buffering');
  }

  schedule() {
    const epoch = this.epoch;
    this.nextAt = Math.max(this.nextAt, this.context.currentTime);
    for (const chunk of this.pending.splice(0)) {
      if (!chunk.frames) continue; // An empty final block is allowed.
      const buffer = this.context.createBuffer(chunk.channels, chunk.frames, chunk.sampleRate);
      for (let channel = 0; channel < chunk.channels; channel++) {
        const output = buffer.getChannelData(channel);
        for (let frame = 0; frame < chunk.frames; frame++) output[frame] = chunk.samples[frame * chunk.channels + channel];
      }
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.connect(this.context.destination);
      this.nodes.add(source);
      source.onended = () => {
        source.disconnect();
        if (this.epoch !== epoch) return;
        this.nodes.delete(source);
        if (!this.nodes.size && !this.pending.length) this.drained();
      };
      source.start(this.nextAt);
      this.nextAt += chunk.frames / chunk.sampleRate;
    }
    if (this.nodes.size) this.report('playing');
    else this.drained();
  }

  drained() {
    if (this.final) {
      this.stopped = true;
      this.report('ended');
      this.onEnded();
    } else {
      this.underruns++;
      this.report('underrun');
    }
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    this.epoch++;
    for (const source of this.nodes) {
      source.onended = null;
      try { source.stop(); } catch (_) {}
      source.disconnect();
    }
    this.nodes.clear();
    this.pending = [];
    this.nextAt = this.context.currentTime;
    this.report('stopped');
  }
}
