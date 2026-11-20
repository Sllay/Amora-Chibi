/**
 * Deterministic temporal contact sheet for bounded native GIF/APNG stickers.
 *
 * Native bytes remain the source of truth: Core receives them as the asset
 * identity, separately from the derived 2x2 PNG used solely for vision.
 * No shell, URLs, or user-provided options reach ffmpeg.
 */
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const GIF_87 = 'GIF87a';
const GIF_89 = 'GIF89a';
const MAX_INPUT_BYTES = 1024 * 1024;
const MAX_OUTPUT_BYTES = 1024 * 1024;
const MAX_FRAMES = 150;
const MAX_DIMENSION = 1024;
const MAX_DURATION_MS = 24_000;
const TIMEOUT_MS = 6_000;

export class TemporalStoryboardError extends Error {
  constructor(code) {
    super(code);
    this.name = 'TemporalStoryboardError';
    this.code = code;
  }
}

function bad() {
  throw new TemporalStoryboardError('TEMPORAL_SOURCE_INVALID');
}
function boundedDimensions(width, height) {
  if (!(width >= 1 && width <= MAX_DIMENSION &&
        height >= 1 && height <= MAX_DIMENSION &&
        width * height <= MAX_DIMENSION * MAX_DIMENSION)) bad();
}
function skipBlocks(bytes, offset) {
  while (offset < bytes.length) {
    const size = bytes[offset++];
    if (size === 0) return offset;
    if (offset + size > bytes.length) bad();
    offset += size;
  }
  bad();
}

function inspectGif(bytes) {
  if (bytes.length < 14 ||
      ![GIF_87, GIF_89].includes(bytes.toString('ascii', 0, 6))) bad();
  const width = bytes.readUInt16LE(6);
  const height = bytes.readUInt16LE(8);
  boundedDimensions(width, height);
  const packed = bytes[10];
  let at = 13 + ((packed & 0x80) ? 3 * (1 << ((packed & 7) + 1)) : 0);
  if (at >= bytes.length) bad();
  let count = 0;
  let terminated = false;
  while (at < bytes.length) {
    const type = bytes[at++];
    if (type === 0x3b) {
      terminated = true;
      break;
    }
    if (type === 0x21) {
      if (at >= bytes.length) bad();
      at++; // extension label
      at = skipBlocks(bytes, at);
      continue;
    }
    if (type !== 0x2c || at + 9 > bytes.length) bad();
    const fw = bytes.readUInt16LE(at + 4);
    const fh = bytes.readUInt16LE(at + 6);
    boundedDimensions(fw, fh);
    const localFlags = bytes[at + 8];
    at += 9 + ((localFlags & 0x80) ? 3 * (1 << ((localFlags & 7) + 1)) : 0);
    if (at >= bytes.length) bad();
    at++; // LZW minimum code size
    at = skipBlocks(bytes, at);
    count++;
    if (count > MAX_FRAMES) throw new TemporalStoryboardError('TEMPORAL_TOO_MANY_FRAMES');
  }
  if (!terminated || count < 2) bad();
  return count;
}

function inspectApng(bytes) {
  if (!bytes.subarray(0, 8).equals(PNG_SIGNATURE)) bad();
  let at = 8;
  let width = 0, height = 0, declared = 0, count = 0, end = false;
  let seenIHDR = false, seenACTL = false;
  while (at + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(at);
    const next = at + 12 + length;
    if (length > MAX_INPUT_BYTES || next > bytes.length) bad();
    const kind = bytes.toString('ascii', at + 4, at + 8);
    const data = at + 8;
    if (kind === 'IHDR') {
      if (seenIHDR || length !== 13 || at !== 8) bad();
      width = bytes.readUInt32BE(data);
      height = bytes.readUInt32BE(data + 4);
      boundedDimensions(width, height);
      seenIHDR = true;
    }
    if (kind === 'acTL') {
      if (!seenIHDR || seenACTL || length !== 8) bad();
      declared = bytes.readUInt32BE(data);
      if (declared < 2 || declared > MAX_FRAMES) bad();
      seenACTL = true;
    }
    if (kind === 'fcTL') {
      if (!seenACTL || length !== 26) bad();
      boundedDimensions(bytes.readUInt32BE(data + 4), bytes.readUInt32BE(data + 8));
      count++;
      if (count > MAX_FRAMES) bad();
    }
    at = next;
    if (kind === 'IEND') {
      if (length !== 0) bad();
      end = true;
      break;
    }
  }
  if (!end || !seenIHDR || !seenACTL || declared !== count) bad();
  return count;
}

/** Read frame counts without decoding compressed pixels. */
export function animationFrameCount(media) {
  const bytes = Buffer.from(media?.bytes || []);
  if (!bytes.length || bytes.length > MAX_INPUT_BYTES) {
    throw new TemporalStoryboardError('TEMPORAL_SOURCE_TOO_LARGE');
  }
  if (media.formatType === 4 && media.mime === 'image/gif') return inspectGif(bytes);
  if (media.formatType === 2 && media.mime === 'image/png') return inspectApng(bytes);
  bad();
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    execFile('ffmpeg', args, {
      timeout: TIMEOUT_MS,
      maxBuffer: 8192,
      shell: false,
      windowsHide: true,
      env: {
        PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
        HOME: tmpdir(),
      },
    }, (error) => {
      if (!error) return resolve();
      reject(new TemporalStoryboardError(
        error.killed || error.code === 'ETIMEDOUT'
          ? 'TEMPORAL_RENDER_TIMEOUT' : 'TEMPORAL_RENDER_FAILED',
      ));
    });
  });
}

/** Up to four deterministic, ordered frame-index samples across the full cycle. */
export function temporalFrameIndices(count) {
  if (!Number.isInteger(count) || count < 2 || count > MAX_FRAMES) bad();
  return [...new Set([
    0, Math.floor((count - 1) / 3),
    Math.floor((2 * (count - 1)) / 3), count - 1,
  ])].sort((a, b) => a - b);
}

export async function renderTemporalStoryboard(media, { run = runFfmpeg } = {}) {
  const count = animationFrameCount(media);
  const indices = temporalFrameIndices(count);
  const dir = await mkdtemp(join(tmpdir(), 'amora-sticker-frames-'));
  try {
    const source = join(dir, media.formatType === 4 ? 'source.gif' : 'source.png');
    const output = join(dir, 'storyboard.png');
    await writeFile(source, media.bytes, { mode: 0o600, flag: 'wx' });
    const select = indices.map(index => `eq(n\\,${index})`).join('+');
    const vf = `select='${select}',scale=240:240:force_original_aspect_ratio=decrease,` +
      `pad=240:240:(ow-iw)/2:(oh-ih)/2:color=white,tile=2x2:nb_frames=${indices.length}`;
    await run([
      '-nostdin', '-hide_banner', '-loglevel', 'error',
      '-threads', '1', '-filter_threads', '1', '-max_alloc', '16777216',
      '-i', source, '-vf', vf, '-frames:v', '1', '-y', output,
    ]);
    const bytes = await readFile(output);
    if (bytes.length < 33 || bytes.length > MAX_OUTPUT_BYTES ||
        !bytes.subarray(0, 8).equals(PNG_SIGNATURE) ||
        bytes.readUInt32BE(16) !== 480 || bytes.readUInt32BE(20) !== 480) {
      throw new TemporalStoryboardError('TEMPORAL_RENDER_INVALID');
    }
    return {
      bytes,
      mime: 'image/png',
      sourceMime: media.mime,
      sourceBytes: media.bytes,
      kind: media.formatType === 4 ? 'gif' : 'apng',
      frameCount: count,
      sampledFrames: indices.length,
    };
  } catch (error) {
    if (error instanceof TemporalStoryboardError) throw error;
    throw new TemporalStoryboardError('TEMPORAL_RENDER_FAILED');
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
