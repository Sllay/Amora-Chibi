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

const MAX_CONSECUTIVE_POLL_ERRORS = 3;
const MAX_PROGRESS_MS = 120_000;

function transientCoreError(error) {
  return ['CORE_TIMEOUT', 'CORE_NETWORK_ERROR'].includes(error?.code)
    || [502, 503, 504].includes(Number(error?.status));
}

/**
 * Best-effort typing signal. Revalidate Core authorization before every send.
 * A transient Core polling failure must not permanently hide typing for a
 * healthy inbound request. No retries on permission/Discord REST failures.
 */
export function startTypingProgress({
  poll, send, isCurrent, onStop = () => {}, onEvent = () => {},
  schedule = setTimeout, cancel = clearTimeout,
  now = () => performance.now(),
}) {
  const started = now();
  let stopped = false;
  let timer = null;
  let polls = 0;
  let wasActive = false;
  let pollErrors = 0;
  function stop(reason = 'cancelled') {
    if (stopped) return;
    stopped = true;
    if (timer !== null) cancel(timer);
    onStop(reason);
  }
  function later(delay) {
    if (stopped) return;
    const remaining = MAX_PROGRESS_MS - (now() - started);
    if (remaining <= 0) { stop('deadline'); return; }
    timer = schedule(tick, Math.min(delay, remaining));
    timer?.unref?.();
  }
  function current() {
    if (stopped) return false;
    if (!isCurrent()) { stop('source_not_current'); return false; }
    if (now() - started >= MAX_PROGRESS_MS) { stop('deadline'); return false; }
    return true;
  }
  async function tick() {
    timer = null;
    if (!current()) return;
    if (++polls > 60) { stop('poll_limit'); return; }
    let active;
    try {
      active = await poll();
    } catch (error) {
      if (!current()) return;
      pollErrors += 1;
      const retry = transientCoreError(error) && pollErrors <= MAX_CONSECUTIVE_POLL_ERRORS;
      onEvent('poll_error', { status: Number(error?.status) || 0, retry, attempt: pollErrors });
      if (retry) later(1000 * (2 ** (pollErrors - 1)));
      else stop('poll_failed');
      return;
    }
    if (!current()) return;
    pollErrors = 0;
    if (active === true) {
      const first = !wasActive;
      wasActive = true;
      try {
        await send();
      } catch (error) {
        if (!stopped) {
          onEvent('send_error', { status: Number(error?.status) || 0 });
          stop('discord_send_failed');
        }
        return;
      }
      if (!current()) return;
      if (first) onEvent('first_sent', {});
      later(7000);
    } else if (wasActive) {
      stop('core_inactive');
    } else {
      later(2000);
    }
  }
  later(500);
  return stop;
}
