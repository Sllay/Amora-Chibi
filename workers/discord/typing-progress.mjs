import { createHash } from 'node:crypto';

// Python uuid5(NAMESPACE_URL, "amora:messaging-turn:" + inbound key).
// This is a correlation identifier, not an authorization credential.
const NAMESPACE_URL = Buffer.from('6ba7b8119dad11d180b400c04fd430c8', 'hex');

export function messagingTurnId(message) {
  const key = [message.platform, message.account_scope, message.conversation_id, message.message_id].join('|');
  const bytes = createHash('sha1').update(NAMESPACE_URL)
    .update('amora:messaging-turn:' + key, 'utf8').digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString('hex');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16),
    hex.slice(16, 20), hex.slice(20)].join('-');
}

/**
 * Best-effort, nonblocking typing signal. No text is sent here.
 * Poll the authorized Core state before each Discord typing call; stop on
 * completion, transport invalidation, errors, or a bounded number of polls.
 * Timers are injectable for deterministic unit tests.
 */
export function startTypingProgress({
  poll, send, isCurrent, onStop = () => {},
  schedule = setTimeout, cancel = clearTimeout,
  now = () => performance.now(),
}) {
  const started = now();
  let stopped = false;
  let timer = null;
  let polls = 0;
  let wasActive = false;
  function stop() {
    if (stopped) return;
    stopped = true;
    if (timer !== null) cancel(timer);
    onStop();
  }
  function later(delay) {
    if (stopped) return;
    timer = schedule(tick, delay);
    timer?.unref?.();
  }
  async function tick() {
    timer = null;
    if (stopped || !isCurrent() || ++polls > 60 || now() - started >= 120_000) {
      stop();
      return;
    }
    try {
      const active = await poll();
      if (stopped || !isCurrent()) { stop(); return; }
      if (active === true) {
        wasActive = true;
        await send();
        later(7000);
      } else if (wasActive) {
        stop();
      } else {
        later(2000);
      }
    } catch {
      // Optional progress never retries a 429/403 or blocks the real answer.
      stop();
    }
  }
  later(500);
  return stop;
}
