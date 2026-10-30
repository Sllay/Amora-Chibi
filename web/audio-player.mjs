// Platform audio only. No synthesis, prosody, identity or conversation ownership.
const PCM_SCHEDULE_QUANTUM_SECONDS = 0.25;

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
  // Safari can receive one HTTP ReadableStream block containing several upstream
  // TTS chunks because proxies are free to coalesce transfer boundaries. Keep
  // that unavoidable transport backlog, but only schedule a bounded Web Audio
  // horizon. Small scheduled quanta refill the horizon as playback advances.
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
    const pendingSeconds = this.pending.reduce((n, block) => n + block.frames / block.sampleRate, 0);
    const scheduledSeconds = Math.max(0, this.nextAt - this.context.currentTime);
    return {
      status: this.status,
      underruns: this.underruns,
      bufferedSeconds: pendingSeconds + scheduledSeconds,
      pendingSeconds,
      scheduledSeconds,
    };
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
    // Own the transport block. A single browser read may exceed maxBufferSeconds
    // after proxy coalescing; schedule() keeps only the playback horizon bounded.
    this.pending.push({ samples: chunk.samples.slice(), sampleRate: chunk.sampleRate, channels: chunk.channels, frames });
    this.format = format;
    this.sequence++;
    this.final = chunk.final;
    if (this.status === 'playing' || this.final || this.state().bufferedSeconds >= this.minBufferSeconds) this.schedule();
    else this.report('buffering');
  }

  schedule() {
    if (this.stopped) return;
    const epoch = this.epoch;
    this.nextAt = Math.max(this.nextAt, this.context.currentTime);
    let scheduledSeconds = Math.max(0, this.nextAt - this.context.currentTime);

    while (this.pending.length && scheduledSeconds < this.maxBufferSeconds) {
      const chunk = this.pending[0];
      if (!chunk.frames) {
        this.pending.shift(); // An empty final transport block is valid.
        continue;
      }

      const capacityFrames = Math.floor((this.maxBufferSeconds - scheduledSeconds) * chunk.sampleRate + 1e-6);
      if (capacityFrames <= 0) break;
      const quantumFrames = Math.max(1, Math.floor(PCM_SCHEDULE_QUANTUM_SECONDS * chunk.sampleRate));
      const frames = Math.min(chunk.frames, capacityFrames, quantumFrames);
      const sampleCount = frames * chunk.channels;
      const samples = chunk.samples.subarray(0, sampleCount);

      const buffer = this.context.createBuffer(chunk.channels, frames, chunk.sampleRate);
      for (let channel = 0; channel < chunk.channels; channel++) {
        const output = buffer.getChannelData(channel);
        for (let frame = 0; frame < frames; frame++) output[frame] = samples[frame * chunk.channels + channel];
      }

      if (frames === chunk.frames) {
        this.pending.shift();
      } else {
        this.pending[0] = {
          ...chunk,
          samples: chunk.samples.subarray(sampleCount),
          frames: chunk.frames - frames,
        };
      }

      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.connect(this.context.destination);
      this.nodes.add(source);
      source.onended = () => {
        source.disconnect();
        if (this.epoch !== epoch) return;
        this.nodes.delete(source);
        // Refill before the scheduled horizon drains completely. This avoids a
        // callback-sized gap even when one HTTP read contained many seconds.
        if (this.pending.length && !this.stopped) this.schedule();
        if (!this.nodes.size && !this.pending.length) this.drained();
      };
      source.start(this.nextAt);
      this.nextAt += frames / chunk.sampleRate;
      scheduledSeconds = Math.max(0, this.nextAt - this.context.currentTime);
    }

    if (this.nodes.size) this.report('playing');
    else if (this.pending.length) this.report('buffering');
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
