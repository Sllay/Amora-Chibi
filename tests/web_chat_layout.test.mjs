import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../web/styles.css', import.meta.url), 'utf8');
const app = readFileSync(new URL('../web/app-v2.js', import.meta.url), 'utf8');

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'm'));
  assert.ok(match, `missing CSS rule ${selector}`);
  return match[1];
}

test('conversation panel is viewport-bounded and history scrolls inside it', () => {
  const conversation = rule('.conversation');
  const messages = rule('.messages');

  assert.match(conversation, /(?:^|;)\s*height\s*:/);
  assert.match(conversation, /(?:^|;)\s*max-height\s*:/);
  assert.match(conversation, /overflow\s*:\s*hidden/);

  // min-height:0 is required for a flex child to shrink instead of stretching
  // the whole conversation card as history grows.
  assert.match(messages, /min-height\s*:\s*0/);
  assert.match(messages, /overflow-y\s*:\s*auto/);
  assert.match(messages, /scroll-behavior\s*:\s*smooth/);
});

test('live message rendering keeps automatic vertical scroll to newest message', () => {
  assert.match(
    app,
    /messages["']\)\.scrollTop\s*=\s*\$\(["']messages["']\)\.scrollHeight/,
  );
});
