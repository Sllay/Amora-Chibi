import test from 'node:test';
import assert from 'node:assert/strict';
import { PcmStreamPlayer, WavPlayer } from '../web/audio-player.mjs';

const identity = { sessionId: 'session', turnId: 'turn', utteranceId: 'utterance' };
function context() {
  const nodes = [];
  const audio = { currentTime: 0, destination: {},
    createBuffer(channels, frames, rate) {
      const data = Array.from({ length: channels }, () => new Float32Array(frames));
      return { duration: frames / rate, getChannelData: channel => data[channel] };
    },
    createBufferSource() {
      const node = { connect() {}, disconnect() {}, start(at) { this.at = at; }, stop() { this.stopped = true; },
        end() { audio.currentTime = this.at + this.buffer.duration; this.onended?.(); } };
      nodes.push(node); return node;
    },
  };
  return { audio, nodes };
}
function chunk(sequence, final = false, frames = 320) {
  return { ...identity, sequence, final, sampleRate: 16000, channels: 1, samples: new Float32Array(frames).fill(0.25) };
}

test('sequential synthetic PCM starts progressively, preserves sample order and ends after the final node', () => {
  const { audio, nodes } = context(); let ended = 0;
  const player = new PcmStreamPlayer(audio, identity, { minBufferSeconds: 0.04, onEnded: () => ended++ });
  const first = chunk(0);
  player.push(first);
  first.samples.fill(0.75); // The sink owns a copy.
  assert.equal(player.state().status, 'buffering');
  player.push(chunk(1));
  assert.equal(nodes.length, 2); // Playback scheduled before final chunk arrives.
  assert.equal(player.state().status, 'playing');
  player.push(chunk(2, true));
  assert.deepEqual(nodes.map(node => node.at), [0, 0.02, 0.04]);
  assert.equal(nodes[0].buffer.getChannelData(0)[0], 0.25);
  nodes[0].end(); nodes[1].end(); assert.equal(ended, 0);
  nodes[2].end(); assert.equal(ended, 1);
  assert.equal(player.state().status, 'ended');
  assert.equal(player.state().underruns, 0);
});

test('underrun is observable and later chunks resume the same stream', () => {
  const { audio, nodes } = context(); const player = new PcmStreamPlayer(audio, identity, { minBufferSeconds: 0 });
  player.push(chunk(0)); nodes[0].end();
  assert.equal(player.state().status, 'underrun');
  assert.equal(player.state().underruns, 1);
  audio.currentTime = 0.2;
  player.push(chunk(1, true));
  assert.equal(nodes[1].at, 0.2);
  nodes[1].end(); assert.equal(player.state().status, 'ended');
});

test('PCM backpressure is bounded and stereo interleaving is preserved', () => {
  const { audio, nodes } = context(); const player = new PcmStreamPlayer(audio, identity, { minBufferSeconds: 0, maxBufferSeconds: 0.1 });
  assert.throws(() => player.push(chunk(0, false, 16000)), /PCM_BUFFER_FULL/);
  player.push({ ...chunk(0, true, 4), channels: 2, samples: Float32Array.of(0.25, 0.5, 0.75, 1) });
  assert.deepEqual([...nodes[0].buffer.getChannelData(0)], [0.25, 0.75]);
  assert.deepEqual([...nodes[0].buffer.getChannelData(1)], [0.5, 1]);
});

test('PCM rejects changed format, identity, sequence, invalid samples and data after final', () => {
  const { audio } = context(); const player = new PcmStreamPlayer(audio, identity);
  for (const change of [{ sequence: 1 }, { turnId: 'wrong' }, { sessionId: 'wrong' }, { channels: 3 },
    { samples: [0] }, { samples: Float32Array.of(NaN) }, { samples: Float32Array.of(2) }]) {
    assert.throws(() => player.push({ ...chunk(0), ...change }), /PCM_/);
  }
  player.push(chunk(0));
  assert.throws(() => player.push({ ...chunk(1), sampleRate: 24000 }), /PCM_FORMAT_CHANGED/);
  player.push(chunk(1, true));
  assert.throws(() => player.push(chunk(2)), /PCM_STREAM_CLOSED/);
});

test('stop cancels scheduled nodes and buffered chunks; stale callbacks cannot end the session', () => {
  const { audio, nodes } = context(); let ended = 0;
  const player = new PcmStreamPlayer(audio, identity, { minBufferSeconds: 0, onEnded: () => ended++ });
  player.push(chunk(0)); player.push(chunk(1, true));
  const oldEnded = nodes[0].onended;
  player.stop(); oldEnded();
  assert.ok(nodes.every(node => node.stopped));
  assert.equal(ended, 0);
  assert.equal(player.state().status, 'stopped');
  assert.equal(player.state().bufferedSeconds, 0);
  assert.throws(() => player.push(chunk(2)), /PCM_STREAM_CLOSED/);
});

test('empty final block closes an empty PCM session without inventing audio', () => {
  const { audio, nodes } = context(); const player = new PcmStreamPlayer(audio, identity);
  player.push(chunk(0, true, 0));
  assert.equal(nodes.length, 0);
  assert.equal(player.state().status, 'ended');
});

test('WAV adapter rejects superseded preparation before constructing an AudioContext', async () => {
  let contexts = 0; const player = new WavPlayer(() => { contexts++; });
  await assert.rejects(player.prepare(new Response(new Uint8Array([1])), () => false), { name: 'AbortError' });
  assert.equal(contexts, 0);
});
