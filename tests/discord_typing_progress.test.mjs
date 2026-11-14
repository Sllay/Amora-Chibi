import test from 'node:test';
import assert from 'node:assert/strict';
import { messagingTurnId, startTypingProgress } from '../workers/discord/typing-progress.mjs';

test('turn correlation matches Python UUID v5', () => {
  assert.equal(messagingTurnId({platform:'discord',account_scope:'primary',
    conversation_id:'channel-1',message_id:'dm-1'}),
  '1548fbdd-61ff-5104-adc0-43f1debb34bf');
});

function harness(states) {
  const queue = [];
  let sent = 0, current = true, stopped = 0, polls = 0, stopReason;
  const events = [];
  const stop = startTypingProgress({
    poll: async () => { polls++; const value = states.shift(); if (value instanceof Error) throw value; return value ?? false; },
    send: async () => { sent++; },
    isCurrent: () => current,
    onStop: reason => { stopped++; stopReason = reason; },
    onEvent: (event, fields) => events.push({event, ...fields}),
    schedule: (fn, delay) => { const item = {fn, delay}; queue.push(item); return item; },
    cancel: item => { const index = queue.indexOf(item); if (index >= 0) queue.splice(index, 1); },
    now: () => 0,
  });
  return {queue, stop, events, reason: () => stopReason, next: async () => { await queue.shift()?.fn(); },
    invalidate: () => { current = false; }, stats: () => ({sent, stopped, polls})};
}

test('typing waits for authorized Core and refreshes at bounded intervals', async () => {
  const h = harness([false, true, true, false]);
  assert.equal(h.queue[0].delay, 500);
  await h.next();
  assert.equal(h.stats().sent, 0);
  assert.equal(h.queue[0].delay, 2000);
  await h.next();
  assert.equal(h.stats().sent, 1);
  assert.equal(h.queue[0].delay, 7000);
  await h.next();
  assert.equal(h.stats().sent, 2);
  await h.next();
  assert.equal(h.stats().stopped, 1);
  assert.equal(h.queue.length, 0);
});

test('completion and edited/deleted source stop without another typing call', async () => {
  const h = harness([true, true]);
  await h.next();
  h.invalidate();
  await h.next();
  assert.deepEqual(h.stats(), {sent:1, stopped:1, polls:1});
  const other = harness([true]);
  other.stop();
  assert.equal(other.queue.length, 0);
  assert.equal(other.stats().sent, 0);
});

test('in-flight poll does not send after cancellation', async () => {
  let resolve, callback, sent = 0;
  const stop = startTypingProgress({
    poll: () => new Promise(r => {resolve=r;}),
    send: async () => {sent++;},
    isCurrent: () => true,
    schedule: fn => {callback=fn;return 1;}, cancel: () => {},
  });
  const running = callback();
  stop();
  resolve(true);
  await running;
  assert.equal(sent, 0);
});

function coreError(code, status = null) {
  return Object.assign(new Error(code), { code, status });
}

test('transient Core timeout before authorization does not silently kill typing', async () => {
  const h = harness([coreError('CORE_TIMEOUT'), false, true, true, false]);
  await h.next();
  assert.equal(h.queue[0].delay, 1000);
  assert.equal(h.stats().sent, 0);
  await h.next();
  assert.equal(h.queue[0].delay, 2000);
  await h.next();
  assert.equal(h.stats().sent, 1);
  await h.next();
  assert.equal(h.stats().sent, 2);
  await h.next();
  assert.equal(h.reason(), 'core_inactive');
  assert.deepEqual(h.events.map(x => x.event), ['poll_error', 'first_sent']);
});

test('transient error after typing still requires fresh active authorization', async () => {
  const h = harness([true, coreError('CORE_HTTP_503', 503), true, false]);
  await h.next();
  await h.next();
  assert.equal(h.stats().sent, 1);
  assert.equal(h.queue[0].delay, 1000);
  await h.next();
  assert.equal(h.stats().sent, 2);
  await h.next();
  assert.equal(h.reason(), 'core_inactive');
});

test('poll errors are bounded and terminal failures never retry', async () => {
  const timeout = () => coreError('CORE_TIMEOUT');
  const h = harness([timeout(), timeout(), timeout(), timeout(), true]);
  for (const delay of [1000, 2000, 4000]) {
    await h.next();
    assert.equal(h.queue[0].delay, delay);
  }
  await h.next();
  assert.equal(h.reason(), 'poll_failed');
  assert.equal(h.queue.length, 0);
  assert.equal(h.stats().sent, 0);
  for (const status of [401, 403, 404, 429]) {
    const terminal = harness([coreError('CORE_HTTP_' + status, status), true]);
    await terminal.next();
    assert.equal(terminal.reason(), 'poll_failed');
    assert.equal(terminal.queue.length, 0);
  }
});

test('source invalidation during retry prevents any further progress request', async () => {
  const h = harness([coreError('CORE_NETWORK_ERROR'), true]);
  await h.next();
  h.invalidate();
  await h.next();
  assert.equal(h.stats().polls, 1);
  assert.equal(h.stats().sent, 0);
  assert.equal(h.reason(), 'source_not_current');
});

test('deadline reached during a successful poll cannot send typing', async () => {
  let now = 0, callback, resolve, sent = 0, reason;
  startTypingProgress({
    poll: () => new Promise(r => {resolve = r;}),
    send: async () => {sent++;},
    isCurrent: () => true,
    now: () => now,
    schedule: fn => {callback = fn; return 1;}, cancel: () => {},
    onStop: value => {reason = value;},
  });
  const running = callback();
  now = 120001;
  resolve(true);
  await running;
  assert.equal(sent, 0);
  assert.equal(reason, 'deadline');
});

test('Discord send failure is terminal, not a Core retry', async () => {
  let callback, reason, polls = 0, sends = 0;
  startTypingProgress({
    poll: async () => {polls++; return true;},
    send: async () => {sends++; throw coreError('RATE_LIMITED', 429);},
    isCurrent: () => true,
    schedule: fn => {callback = fn; return 1;}, cancel: () => {},
    onStop: value => {reason = value;},
  });
  await callback();
  assert.equal(reason, 'discord_send_failed');
  assert.equal(polls, 1);
  assert.equal(sends, 1);
});
