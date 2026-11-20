/** Synthetic, public-domain RGBA pixel test; run only in CI container. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { compositeTransparentImage } from './incoming-image-transform.mjs';

// 4x4 valid transparent PNG with two colored opaque pixels.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAGklEQVR4nGNgwAaeMcj9xxCUk8MiyMDAwAAAd+8DXXl97WYAAAAASUVORK5CYII=',
  'base64',
);
const prepared = await compositeTransparentImage(PNG, 'image/png');
assert.equal(prepared.mime, 'image/webp');
assert.ok(prepared.bytes.length >= 12);
const folder = await mkdtemp(join(tmpdir(), 'amora-alpha-ci-'));
try {
  const file = join(folder, 'perception.webp');
  await writeFile(file, prepared.bytes, { mode: 0o600 });
  // RGB with no alpha, so the provider cannot silently flatten against black.
  const bands = Number(execFileSync('vipsheader', ['-f', 'bands', file], { encoding: 'utf8' }).trim());
  const average = Number(execFileSync('vips', ['avg', file], { encoding: 'utf8' }).trim());
  assert.equal(bands, 3);
  assert.ok(average > 95, 'alpha preview should show a neutral light background');
  console.log('vision-alpha-opaque-smoke-ok');
} finally {
  await rm(folder, { recursive: true, force: true });
}
