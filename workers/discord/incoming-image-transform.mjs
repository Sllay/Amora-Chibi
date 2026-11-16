/**
 * Bounded image transcoding for oversized Discord artwork.
 *
 * Uses Alpine's maintained libvips CLI, not a custom image parser or Discord
 * undocumented resize parameters. It runs without a shell, with fixed inputs
 * and options, in a disposable private directory. Bytes and paths never log.
 */
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const MAX_VISION_WIRE_BYTES = 4 * 1024 * 1024;
const TRANSFORM_BUDGET_MS = 8_000;
const SOURCE_EXTENSIONS = Object.freeze({
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
});
const PRESETS = Object.freeze([
  { size: 2560, quality: 92 },
  { size: 2048, quality: 90 },
  { size: 1536, quality: 86 },
  { size: 1280, quality: 82 },
]);

export class ImageTransformError extends Error {
  constructor(code) {
    super(code);
    this.name = 'ImageTransformError';
    this.code = code;
  }
}

function runVips(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    execFile('vipsthumbnail', args, {
      timeout: timeoutMs,
      maxBuffer: 8192,
      shell: false,
      windowsHide: true,
      env: {
        PATH: process.env.PATH || '/usr/local/bin:/usr/bin:/bin',
        HOME: tmpdir(),
        // Avoid cache pressure in the constrained Discord worker.
        VIPS_CONCURRENCY: '1',
        VIPS_DISC_THRESHOLD: String(32 * 1024 * 1024),
      },
    }, (error) => {
      if (!error) return resolve();
      reject(new ImageTransformError(
        error.killed || error.code === 'ETIMEDOUT'
          ? 'IMAGE_TRANSFORM_TIMEOUT' : 'IMAGE_TRANSFORM_FAILED',
      ));
    });
  });
}

/**
 * Convert ONLY a previously authenticated native PNG/JPEG/WebP raster image.
 * Original bytes remain unchanged for small images (handled by caller).
 * The conversion keeps transparency when supported, preserves aspect ratio,
 * and shrinks only if needed; no disk artifacts survive any outcome.
 */
export async function transcodeOversizedImage(bytes, mime, {
  maxBytes = MAX_VISION_WIRE_BYTES,
  now = Date.now,
  run = runVips,
} = {}) {
  const ext = SOURCE_EXTENSIONS[mime];
  if (!ext || !Buffer.isBuffer(bytes) || bytes.length < 12) {
    throw new ImageTransformError('IMAGE_TRANSFORM_INPUT_INVALID');
  }
  const deadline = now() + TRANSFORM_BUDGET_MS;
  const dir = await mkdtemp(join(tmpdir(), 'amora-vision-'));
  try {
    const source = join(dir, 'native.' + ext);
    await writeFile(source, bytes, { mode: 0o600, flag: 'wx' });
    for (const preset of PRESETS) {
      const left = deadline - now();
      if (left < 200) throw new ImageTransformError('IMAGE_TRANSFORM_TIMEOUT');
      const target = join(dir, 'visual-' + preset.size + '.webp');
      try {
        // libvips supports bracket options on the output filename; the file
        // itself is written at target, with no brackets in its actual path.
        await run([
          source, '--size', String(preset.size),
          '--path', target + '[Q=' + preset.quality + ',strip]',
        ], Math.min(4000, left));
        const info = await stat(target);
        if (!info.isFile() || !info.size) {
          throw new ImageTransformError('IMAGE_TRANSFORM_FAILED');
        }
        if (info.size > maxBytes) continue;
        const output = await readFile(target);
        if (output.subarray(0, 4).toString('ascii') !== 'RIFF'
            || output.subarray(8, 12).toString('ascii') !== 'WEBP') {
          throw new ImageTransformError('IMAGE_TRANSFORM_SIGNATURE_INVALID');
        }
        return { bytes: output, mime: 'image/webp' };
      } catch (error) {
        if (error instanceof ImageTransformError) throw error;
        throw new ImageTransformError('IMAGE_TRANSFORM_FAILED');
      }
    }
    throw new ImageTransformError('IMAGE_TRANSFORM_SIZE_EXCEEDED');
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
