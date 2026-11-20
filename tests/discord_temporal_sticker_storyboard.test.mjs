import assert from 'node:assert/strict';
import test from 'node:test';
import { access, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';

import {
  animationFrameCount, temporalFrameIndices, renderTemporalStoryboard,
  TemporalStoryboardError,
} from '../workers/discord/animation-storyboard.mjs';

// Compact, original four-frame artwork fixtures created without network I/O.
const GIF = Buffer.from('R0lGODlhQABAAIEAAP////8AAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQICQAAACwAAAAAQABAAAAIlwABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatzIsaPHjyBDihxJsqTJkyhTqlzJsqXLlzBjypxJU2WAmzdrEsTJMyfNnkB9wgxKNCbRoEOPInWpdCnLpkWfQgXacipVqVZxVs2qdWvWl1wDJJ1q1KpMsj+j6gTQc63bt3Djyp1Lt67du3jz6t3Lt6/fv4ADCx4MICAAIfkECAwAAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSZNmgJs3axLEyTOnzZ5AA8gMSjQm0aAwjxZ1qRRpy6ZLV0J1KnVqz6dWeWLN6pMlV6FMuSa1apRs2aY1j+oceHWt27dw48qdS7eu3bt48+rdy7ev37+AAwvOGxAAIfkECA4AAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSbNmwgA4cdocmLOnTpo+g/6EKbRozKJCiSJN6nIpU5ZOjUKNGrQl1apTr+a0qnUrV60vuwZQSvXoVZllgUrd6XOn27dw48qdS7eu3bt48+rdy7ev37+AAwuGGxAAIfkECBEAAAAsAAAAAEAAQACB/////wAAAAAAAAAACJYAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcyLGjx48gQ4ocSbKkyZMoU6pcybKly5cwY8qcSbPmxAA4cdocmLOnTpo+g/6EKbRozKJCiSJN6nIpU5ZOjUKNGrQl1apTr+a0qnUrV60vuwZQSvXoVZllgUrd6XOn27dw48qdS7eu3bt48+rdy7ev37+AAwuGGRAAOw==', 'base64');
const APNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAACGFjVEwAAAAEAAAAAHzNZtAAAAAaZmNUTAAAAAAAAABAAAAAQAAAAAAAAAAAAAkAZAIA85ozXAAAAPtJREFUeJztmEEKwyAQANfS/3/Znry0arQRhiUzx6SB2VEkTam11ngwL1qAxgC0AI0BaAEaA9ACNAWgBGgMQAvQGIAWoDEALUBjAFqAxgC0AI0BaAGa9/YTpfxeS/xheT1Ab/DvewlDXAeYDT76baIQ8zNgZ/gTzwE8/hAcB7i7ikl2gTuge/XU6iXYBe4AWoDGALQATT/AqTe5BG+E7oDhnburl2D1I9wBFwH+XcUkqx+x8ne4DbPyVpdo8Mb6B5FZiISDN/Y/iSUetoeHIC1AYwBagMYAtACNAWgBGgPQAjQGoAVoDEAL0BiAFqAxAC1AYwBagMYAtADNBwaUIIYFR1g3AAAAGmZjVEwAAAABAAAAQAAAAEAAAAAAAAAAAAADABkCAH5wOioAAAD+ZmRBVAAAAAJ4nO3YwQ6CMBAGYWt8/1euJy6KRO2SbxP+OSqQ2WlDgDHnnLcLc9cCmgTQApoE0AKaBNACmgTQApoE0AKaBNACmgTQApoE0AKaBNACmgTQAprHaVce4/23hh+g6wPsDf76X6MQdQGOBv90bIMQNfeAX4avOK+Qy98E1wOsriLeBdkBS2dXrR7cBdkBWkCTAFpAsxag6kkOPhFmByxfYXX18PtAdkDJVf5dxQZvg3Wvw9sw3zzVNRh8o/6DyFGIRoNvnPdJrOGwe+QmqAU0CaAFNAmgBTQJoAU0CaAFNAmgBTQJoAU0CaAFNAmgBTQJoAU0CaAFNE+mmSCGIdDzOgAAABpmY1RMAAAAAwAAACcAAAATAAAABQAAABYABwAyAgCZcXyIAAAAf2ZkQVQAAAAEeJztljsOwCAMQ5OK+1/ZXVo1ChS1+bAEj4Dxk5eEAYBWibk/m8S3RJRHIyh9N4DMhZtBvb0VkEcwTh/m8OXBBSgHztqa8hdrztua+KdYc4HacFbFw0WNaqBac0T+9i5/weaI7O0JX+7KdAd9mRrL9zkd/HMTPgHgyR4qJh0/SAAAABpmY1RMAAAABQAAACcAAAATAAAADwAAABYAEQBkAgARhWZWAAAAf2ZkQVQAAAAGeJztljsOwCAMQ5OK+1/ZXVo1ChS1+bAEj4Dxk5eEAYBWibk/m8S3RJRHIyh9N4DMhZtBvb0VkEcwTh/m8OXBBSgHztua+KdYc4HacFbFw0WNaqBac0T+9i5/weaI7O0JX+7KdAd9mRrL9zkd/HMTPgHgyR4qzhDKYgAAAABJRU5ErkJggg==', 'base64');
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
