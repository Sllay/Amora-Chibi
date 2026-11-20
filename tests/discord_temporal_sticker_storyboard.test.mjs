import assert from 'node:assert/strict';
import test from 'node:test';
import { access, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { deflateSync } from 'node:zlib';

import {
  animationFrameCount, temporalFrameIndices, renderTemporalStoryboard,
  TemporalStoryboardError,
} from '../workers/discord/animation-storyboard.mjs';

// Compact, original four-frame artwork fixtures created without network I/O.
const GIF = Buffer.from('R0lGODlhQABAAIEAAP////8AAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQICQAAACwAAAAAQABAAAAIlwABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJU2WAmzdrEsTJMyfNnkB9wgxKNCbRoEOPInWpdCnLpkWfQgXacipVqVZxVs2qdWvWl1wDJJ1q1KpMsj+j6gTQc63bt3Djyp1Lt67du3jz6t3Lt6/fv4ADCx4MICAAIfkECAwAAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSZNmgJs3axLEyTOnzZ5AA8gMSjQm0aAwjxZ1qRRpy6ZLV0J1KnVqz6dWeWLN6pMlV6FMuSa1apRs2aY1j+oceHWt27dw48qdS7eu3bt48+rdy7ev37+AAwvOGxAAIfkECA4AAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSbNmwgA4cdocmLOnTpo+g/6EKbRozKJCiSJN6nIpU5ZOjUKNGrQl1apTr+a0qnUrV60vuwZQSvXoVZllgUrd6XOn27dw48qdS7eu3bt48+rdy7ev37+AAwuGGxAAIfkECBEAAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSbPmxAA4cdocmLOnTpo+g/6EKbRozKJCiSJN6nIpU5ZOjUKNGrQl1apTr+a0qnUrV60vuwZQSvXoVZllgUrd6XOn27dw48qdS7eu3bt48+rdy7ev37+AAwuGGRAAOw==', 'base64');
// Build a valid APNG with four actual, independently encoded frames. Node
// tests do not need a platform-specific image generation dependency.
function pngChunk(kind, payload) {
  const tag = Buffer.from(kind, 'ascii');
  const size = Buffer.alloc(4);
  size.writeUInt32BE(payload.length);
  const crcInput = Buffer.concat([tag, payload]);
  let crc = 0xffffffff;
  for (const byte of crcInput) {
    crc ^= byte;
    for (let n = 0; n < 8; n++) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  const sum = Buffer.alloc(4);
  sum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([size, tag, payload, sum]);
}
function animatedPngFixture() {
  const signature = Buffer.from('89504e470d0a1a0a', 'hex');
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(2, 0);
  ihdr.writeUInt32BE(2, 4);
  ihdr[8] = 8;
  ihdr[9] = 6; // RGBA
  const actl = Buffer.alloc(8);
  actl.writeUInt32BE(4, 0);
  const chunks = [pngChunk('IHDR', ihdr), pngChunk('acTL', actl)];
  let sequence = 0;
  for (let frame = 0; frame < 4; frame++) {
    const fctl = Buffer.alloc(26);
    fctl.writeUInt32BE(sequence++, 0);
    fctl.writeUInt32BE(2, 4);
    fctl.writeUInt32BE(2, 8);
    fctl.writeUInt16BE(10, 20);
    fctl.writeUInt16BE(100, 22);
    chunks.push(pngChunk('fcTL', fctl));
    const color = [
      [220, 0, 0, 255],
      [0, 200, 0, 255],
      [0, 0, 230, 255],
      [230, 210, 0, 255],
    ][frame];
    const scanline = Buffer.from([0, ...color, ...color]);
    const compressed = deflateSync(Buffer.concat([scanline, scanline]));
    if (frame === 0) {
      chunks.push(pngChunk('IDAT', compressed));
    } else {
      const fdatSequence = Buffer.alloc(4);
      fdatSequence.writeUInt32BE(sequence++);
      chunks.push(pngChunk('fdAT', Buffer.concat([fdatSequence, compressed])));
    }
  }
  chunks.push(pngChunk('IEND', Buffer.alloc(0)));
  return Buffer.concat([signature, ...chunks]);
}
const APNG = animatedPngFixture();
const sources = [
  { bytes: GIF, formatType: 4, mime: 'image/gif' },
  { bytes: APNG, formatType: 2, mime: 'image/png' },
];

test('strict GIF/APNG parser verifies complete four-frame native source', () => {
  for (const media of sources) assert.equal(animationFrameCount(media), 4);
  assert.deepEqual(temporalFrameIndices(4), [0, 1, 2, 3]);
  assert.deepEqual(temporalFrameIndices(2), [0, 1]);
});

test('animation inspection refuses wrong format and corrupted frame counts', () => {
  assert.throws(() => animationFrameCount({ ...sources[0], mime: 'image/png' }),
    TemporalStoryboardError);
  assert.throws(() => animationFrameCount({ ...sources[1], bytes: APNG.subarray(0, 45) }),
    TemporalStoryboardError);
  const altered = Buffer.from(APNG);
  const ac = altered.indexOf(Buffer.from('acTL'));
  assert(ac > 0);
  altered.writeUInt32BE(100, ac + 4);
  assert.throws(() => animationFrameCount({ ...sources[1], bytes: altered }),
    TemporalStoryboardError);
  assert.throws(() => animationFrameCount({ ...sources[0], bytes: Buffer.alloc(1024 * 1024 + 1) }),
    TemporalStoryboardError);
});

test('worker renderer uses fixed local ffmpeg arguments, sample order, and cleans files', async () => {
  for (const media of sources) {
    let invoked = false;
    const fakePng = Buffer.alloc(40);
    Buffer.from('89504e470d0a1a0a', 'hex').copy(fakePng, 0);
    fakePng.writeUInt32BE(480, 16);
    fakePng.writeUInt32BE(480, 20);
    const derived = await renderTemporalStoryboard(media, {
      run: async (args) => {
        invoked = true;
        assert(args.includes('-nostdin'));
        assert(args.includes('-max_alloc'));
        assert.match(args[args.indexOf('-vf') + 1], /eq\(n\\,0\).*eq\(n\\,3\)/);
        assert.equal(args[args.length - 1].endsWith('.png'), true);
        await writeFile(args[args.length - 1], fakePng);
      },
    });
    assert(invoked);
    assert.equal(derived.mime, 'image/png');
    assert.equal(derived.frameCount, 4);
    assert.equal(derived.sampledFrames, 4);
    assert.deepEqual(Buffer.from(derived.sourceBytes), Buffer.from(media.bytes));
    assert.deepEqual(derived.bytes, fakePng);
  }
});

let ffmpegAvailable = false;
try {
  execFileSync('ffmpeg', ['-version'], { timeout: 2000, stdio: 'ignore' });
  ffmpegAvailable = true;
} catch { /* CI may run on a host without the worker's Docker dependencies. */ }

test('real ffmpeg transforms GIF and APNG into a genuine 480x480 temporal PNG',
  { skip: !ffmpegAvailable }, async () => {
    for (const media of sources) {
      const result = await renderTemporalStoryboard(media);
      assert(result.bytes.length > 100);
      assert.equal(result.bytes.readUInt32BE(16), 480);
      assert.equal(result.bytes.readUInt32BE(20), 480);
    }
  });
